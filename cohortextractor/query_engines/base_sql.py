import contextlib
import os
from collections import defaultdict

import sqlalchemy
import sqlalchemy.dialects.mssql
import sqlalchemy.schema
import sqlalchemy.types
from sqlalchemy.sql.expression import type_coerce

from ..query_language import (
    Codelist,
    Column,
    Comparator,
    DateDifferenceInYears,
    FilteredTable,
    QueryNode,
    Row,
    Table,
    Value,
    ValueFromAggregate,
    ValueFromCategory,
    ValueFromFunction,
    ValueFromRow,
)
from ..sqlalchemy_drivers import set_driver
from .base import BaseQueryEngine


DEFAULT_TYPES = {
    "boolean": sqlalchemy.types.Boolean,
    "date": sqlalchemy.types.Date,
    "datetime": sqlalchemy.types.DateTime,
    "float": sqlalchemy.types.Float,
    "integer": sqlalchemy.types.Integer,
    "varchar": sqlalchemy.types.Text,
    "code": sqlalchemy.types.Text,
}


def get_joined_tables(query):
    """
    Given a query object return a list of all tables referenced
    """
    tables = []
    from_exprs = list(query.get_final_froms())
    while from_exprs:
        next_expr = from_exprs.pop()
        if isinstance(next_expr, sqlalchemy.sql.selectable.Join):
            from_exprs.extend([next_expr.left, next_expr.right])
        else:
            tables.append(next_expr)
    # The above algorithm produces tables in right to left order, but it makes
    # more sense to return them as left to right
    tables.reverse()
    return tables


def get_primary_table(query):
    """
    Return the left-most table referenced in the query
    """
    return get_joined_tables(query)[0]


class BaseSQLQueryEngine(BaseQueryEngine):

    sqlalchemy_dialect = NotImplemented

    custom_types = {}

    # No limit by default although some DBMSs may impose one
    max_rows_per_insert = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        assert cls.sqlalchemy_dialect != NotImplemented
        cls.type_map = {**DEFAULT_TYPES, **cls.custom_types}

    def __init__(self, column_definitions, backend):
        super().__init__(column_definitions, backend)
        self._engine = None
        # If no "population" was specified in the column definitions, use a default value
        # which just selects rows that exist by patient_id from the default population
        # table (practice_registrations)
        if "population" not in column_definitions:
            column_definitions["population"] = ValueFromAggregate(
                source=Table(name="practice_registrations"),
                function="exists",
                column="patient_id",
            )
        # Walk the nodes and identify output groups
        all_nodes = self.get_all_query_nodes(column_definitions)
        self.output_groups = self.get_output_groups(all_nodes)
        self.codelists = [node for node in all_nodes if isinstance(node, Codelist)]
        self.codelist_tables = {}
        self.codelist_tables_queries = []
        self.output_group_tables = {}
        self.output_group_tables_queries = {}

    #
    # QUERY DAG METHODS AND NODE INTERACTION
    #
    def get_output_groups(self, all_nodes):
        """
        Walk over all nodes in the query DAG looking for output nodes (leaf nodes which
        represent a value or a column of values) and group them together by "type" and
        "source" (source being the parent node from which they are derived). Each such
        group of outputs can be generated by a single query so we want them grouped together.
        """
        output_groups = defaultdict(list)
        for node in all_nodes:
            if self.is_output_node(node):
                output_groups[self.get_output_group(node)].append(node)
        return output_groups

    def get_all_query_nodes(self, column_definitions):
        """
        Return a list of all QueryNodes used in the supplied column_definitions
        in topological order (i.e. a node will never be referenced before it
        appears). We need this so that we construct temporary tables in the
        right order and never try to reference a table which we haven't yet
        populated.
        """
        # Given the way the query DAG is currently constructed we can use a
        # simple trick to get the nodes in topological order. We exploit the
        # fact that the Python-based DSL will naturally enforce a topological
        # order on the leaf nodes (you can't reference variables before you've
        # defined them). Then for each leaf node we traverse depth-first,
        # returning parents before their children. Further down the line we
        # might need to this "properly" (maybe using the networkx library?) but
        # this will do us for now.
        leaf_nodes = column_definitions.values()
        return self.walk_query_dag(leaf_nodes)

    def walk_query_dag(self, nodes):
        def recurse(nodes, seen):
            for node in nodes:
                yield from recurse(self.get_parent_nodes(node), seen)
                if node not in seen:
                    seen.add(node)
                    yield node

        return list(recurse(nodes, set()))

    def get_parent_nodes(self, node):
        if hasattr(node, "definitions"):
            yield from self.list_parent_nodes_from_category_definitions(
                node.definitions.values()
            )
        if hasattr(node, "source"):
            yield node.source
        if hasattr(node, "value") and isinstance(node.value, QueryNode):
            yield node.value
        if hasattr(node, "arguments"):
            for arg in node.arguments:
                if isinstance(arg, QueryNode):
                    yield arg

    @staticmethod
    def is_output_node(node):
        return isinstance(node, (ValueFromRow, ValueFromAggregate, Column))

    @staticmethod
    def is_category_node(node):
        return isinstance(node, ValueFromCategory)

    def get_output_group(self, node):
        assert self.is_output_node(node)
        return type(node), node.source

    @staticmethod
    def get_output_column_name(node):
        if isinstance(node, ValueFromAggregate):
            return f"{node.column}_{node.function}"
        elif isinstance(node, (ValueFromRow, Column)):
            return node.column
        else:
            raise TypeError(f"Unhandled type: {node}")

    def get_parent_nodes_from_category_definitions(
        self, definitions, parent_nodes=None
    ):
        """
        Get all the referenced parent nodes for category definitions.  If a category
        definition (Comparator) has a LHS which is not itself a Comparator,
        it will be a query node or a function-generated node
        """
        parent_nodes = parent_nodes or set()
        for definition in definitions:
            if isinstance(definition.lhs, Value):
                # A ValueFromFunction is not itself a node that is derieved directly from a
                # temporary table.  We look for referenced nodes in its arguments
                definition_parent_nodes = (
                    definition.lhs.arguments
                    if isinstance(definition.lhs, ValueFromFunction)
                    else [definition.lhs]
                )
                for node in definition_parent_nodes:
                    if isinstance(node, Value):
                        parent_nodes.add(node)
            else:
                parent_nodes = self.get_parent_nodes_from_category_definitions(
                    [definition.lhs], parent_nodes
                )

            if isinstance(definition.rhs, Comparator):
                parent_nodes = self.get_parent_nodes_from_category_definitions(
                    [definition.rhs], parent_nodes
                )

        return parent_nodes

    def list_parent_nodes_from_category_definitions(self, definitions):
        # Sort the parent nodes to ensure consistent order.  We have to use a custom sort key
        # here because parent nodes are Values with overloaded lt/gt operators.

        # Note that we sort on column name first, and source as a tie-breaker in the event of
        # two nodes with the same column name.  x.source can be a Row in the case of a truthy
        # Comparator e.g. where a category definition looks something like {"x": code1, "y": code2}
        # and code1/code2 are ValueFromRow.  In this case we can't use the source Row as a
        # sort key, so we use its repr instead.
        return sorted(
            self.get_parent_nodes_from_category_definitions(definitions),
            key=lambda x: (x.column, repr(x.source)),
        )

    def get_node_list(self, node):
        """For a single node, get a list of it and all its parents in order"""
        node_list = []
        while True:
            node_list.append(node)
            if type(node) is Table:
                break
            else:
                node = node.source
        node_list.reverse()
        return node_list

    #
    # DATABASE CONNECTION
    #
    @property
    def engine(self):
        if self._engine is None:
            engine_url = sqlalchemy.engine.make_url(self.backend.database_url)
            engine_url = set_driver(engine_url)
            self._engine = sqlalchemy.create_engine(engine_url, future=True)
        return self._engine

    def create_output_group_tables(self):
        """Queries to generate and populate interim tables for each output"""
        # For each group of "output nodes" (roughly, "nodes which we know how
        # to build a single select query for"), build a SQLAlchemy query to get
        # their values.
        for i, (group, output_nodes) in enumerate(self.output_groups.items()):
            query = self.get_query_expression(group, output_nodes)
            self.output_group_tables_queries[group] = query
            # Create a Table object representing a temporary table into which
            # we'll write the results of the query
            table_name = self.get_temp_table_name(f"group_table_{i}")
            columns = [
                sqlalchemy.Column(c.name, c.type) for c in query.selected_columns
            ]
            table = sqlalchemy.Table(
                table_name,
                sqlalchemy.MetaData(),
                *columns,
            )
            self.output_group_tables[group] = table

    def create_codelist_tables(self):
        """
        For each codelist in use, build a SQLAlchemy representation of the
        temporary table needed to store that codelist and then generate the
        queries necessary to create and populate those tables
        """
        for n, codelist in enumerate(self.codelists):
            codes = codelist.codes
            max_code_len = max(map(len, codes))
            collation = "Latin1_General_BIN"
            table_name = self.get_temp_table_name(f"codelist_{n}")
            table = sqlalchemy.Table(
                table_name,
                sqlalchemy.MetaData(),
                sqlalchemy.Column(
                    "code",
                    sqlalchemy.types.String(max_code_len, collation=collation),
                    nullable=False,
                ),
                sqlalchemy.Column(
                    "system",
                    sqlalchemy.types.String(6),
                    nullable=False,
                ),
            )
            self.codelist_tables[codelist] = table
            # Constuct the queries needed to create and populate this table
            self.codelist_tables_queries.append(sqlalchemy.schema.CreateTable(table))
            for codes_batch in split_list_into_batches(
                codes, size=self.max_rows_per_insert
            ):
                insert_query = table.insert().values(
                    [(code, codelist.system) for code in codes_batch]
                )
                self.codelist_tables_queries.append(insert_query)

    def get_temp_table_name(self, table_name):
        """
        Return a table name based on `table_name` but suitable for use as a
        temporary table.

        It's the caller's responsibility to ensure `table_name` is unique
        within this session; it's this function's responsibility to ensure it
        doesn't clash with any concurrent extracts
        """
        raise NotImplementedError()

    def get_select_expression(self, base_table, columns):
        # every table must have a patient_id column; select it and the specified columns
        columns = sorted({"patient_id"}.union(columns))
        table_expr = self.backend.get_table_expression(base_table.name)
        try:
            column_objs = [table_expr.c[column] for column in columns]
        except KeyError as unknown_key:
            raise KeyError(
                f"Column {unknown_key} not found in table '{base_table.name}'"
            )

        query = sqlalchemy.select(column_objs).select_from(table_expr)
        return query

    def get_query_expression(self, group, output_nodes):
        """
        From a group of output nodes that represent the route to a single output value,
        generate the query that will return the value from its source table(s)
        """
        output_type, query_node = group

        # Queries (currently) always have a linear structure so we can
        # decompose them into a list
        node_list = self.get_node_list(query_node)
        # The start of the list should always be an unfiltered Table
        base_table = node_list.pop(0)
        assert isinstance(base_table, Table)

        # If there's an operation applied to reduce the results to a single row
        # per patient, then that will be the final element of the list
        row_selector = None
        if issubclass(output_type, ValueFromRow):
            row_selector = node_list.pop()
            assert isinstance(row_selector, Row)

        # All remaining nodes should be filter operations
        filters = node_list
        assert all(isinstance(filter_node, FilteredTable) for filter_node in filters)

        # Get all the required columns from the base table
        selected_columns = {node.column for node in output_nodes}
        query = self.get_select_expression(base_table, selected_columns)
        # Apply all filter operations
        for filter_node in filters:
            query = self.apply_filter(query, filter_node)

        # Apply the row selector to select the single row per patient
        if row_selector is not None:
            query = self.apply_row_selector(
                query,
                sort_columns=row_selector.sort_columns,
                descending=row_selector.descending,
            )

        if issubclass(output_type, ValueFromAggregate):
            query = self.apply_aggregates(query, output_nodes)

        return query

    def get_population_table_query(self, population):
        """Build the query that selects the patient population we're interested in"""
        is_included, tables = self.get_value_expression(population)
        assert len(tables) == 1
        population_table = tables[0]
        return (
            sqlalchemy.select([population_table.c.patient_id.label("patient_id")])
            .select_from(population_table)
            .where(is_included == True)  # noqa: E712
        )

    def build_condition_statement(self, comparator):
        """
        Traverse a comparator's left and right hand sides in order and build the nested
        condition statement
        """
        if comparator.connector is not None:
            assert isinstance(comparator.lhs, Comparator) and isinstance(
                comparator.rhs, Comparator
            )
            left_conditions = self.build_condition_statement(comparator.lhs)
            right_conditions = self.build_condition_statement(comparator.rhs)
            connector = getattr(sqlalchemy, comparator.connector)
            condition_statement = connector(left_conditions, right_conditions)
        else:
            lhs, _ = self.get_value_expression(comparator.lhs)
            method = getattr(lhs, comparator.operator)
            condition_statement = method(comparator.rhs)

        if comparator.negated:
            condition_statement = sqlalchemy.not_(condition_statement)

        return condition_statement

    def get_value_expression(self, value):
        """
        Given a single value output node, select it from its interim table(s)
        Return the expression to select it, and the table(s) to select it from
        """
        tables = ()
        value_expr = value
        if self.is_category_node(value):
            category_definitions = value.definitions.copy()
            all_category_referenced_nodes = (
                self.list_parent_nodes_from_category_definitions(
                    category_definitions.values()
                )
            )
            tables = tuple(
                self.output_group_tables[self.get_output_group(query_node)]
                for query_node in all_category_referenced_nodes
            )
            category_mapping = {}
            for label, category_definition in category_definitions.items():
                # A category definition is always a single Comparator, which may contain
                # nested Comparators
                condition_statement = self.build_condition_statement(
                    category_definition
                )
                category_mapping[label] = condition_statement
            value_expr = self.get_case_expression(category_mapping, value.default)
        elif self.is_output_node(value):
            table = self.output_group_tables[self.get_output_group(value)]
            column = self.get_output_column_name(value)
            value_expr = table.c[column]
            tables = (table,)
        elif isinstance(value, Codelist):
            codelist_table = self.codelist_tables[value]
            value_expr = sqlalchemy.select(codelist_table.c.code).scalar_subquery()
        elif isinstance(value, ValueFromFunction):
            value_expr, tables = self.get_expression_for_value_from_function(value)
        return value_expr, tables

    def get_case_expression(self, mapping, default):
        return sqlalchemy.case(
            [(expression, label) for label, expression in mapping.items()],
            else_=default,
        )

    def get_expression_for_value_from_function(self, value):
        argument_expressions = []
        tables = set()
        for arg in value.arguments:
            arg_expr, arg_tables = self.get_value_expression(arg)
            argument_expressions.append(arg_expr)
            tables.update(arg_tables)

        # TODO: I'd quite like to build this map by decorating the methods e.g.
        #
        #   @handler_for(DateDifferenceInYears)
        #   def my_handle_fun(...)
        #
        # but the simple thing will do for now.
        class_method_map = {DateDifferenceInYears: self.date_difference_in_years}

        try:
            method = class_method_map[value.__class__]
        except KeyError:
            raise ValueError(f"Unsupported function: {value}")
        value_expression = method(*argument_expressions)

        return value_expression, tuple(tables)

    def date_difference_in_years(self, start_date, end_date):
        Date = self.type_map["date"]
        start_date = type_coerce(start_date, Date())
        end_date = type_coerce(end_date, Date())
        # `literal_column` doesn't seem quite the right construct here, but I
        # need SQLAlchemy to generate the string "year" without quotes, and
        # this seems to do the trick
        YEAR = sqlalchemy.literal_column("year")
        # The year difference here is just the difference between the year
        # components of the dates and takes no account of the month or day
        year_diff = sqlalchemy.func.datediff(
            YEAR, start_date, end_date, type_=sqlalchemy.types.Integer()
        )
        # so we add the resulting number of years back on to the start date
        start_date_plus_year_diff = sqlalchemy.func.dateadd(
            YEAR, year_diff, start_date, type_=Date()
        )
        # and then adjust it down by one year if this takes us past our end date
        return sqlalchemy.case(
            (start_date_plus_year_diff > end_date, year_diff - 1), else_=year_diff
        )

    def apply_aggregates(self, query, aggregate_nodes):
        """
        For each aggregate node, get the query that will select it with its generated
        column label, plus the patient id column, and then group by the patient id.

        e.g. For the default population exists query, it will select patient_id as a column
        labelled patient_id_exists from the entire column of patient_id and then group
         by patient id; i.e.

        SELECT practice_registrations.patient_id, :param_1 AS patient_id_exists
        FROM (SELECT PatientId AS patient_id FROM practice_registrations) AS practice_registrations
        GROUP BY practice_registrations.patient_id

        """
        columns = [
            self.get_aggregate_column(query, aggregate_node)
            for aggregate_node in aggregate_nodes
        ]
        query = query.with_only_columns([query.selected_columns.patient_id] + columns)
        query = query.group_by(query.selected_columns.patient_id)

        return query

    def get_aggregate_column(self, query, aggregate_node):
        """
        For an aggregate node, build the column to hold its value
        Aggregate column names are a combination of column and aggregate function,
        e.g. "patient_id_exists"
        """
        output_column = self.get_output_column_name(aggregate_node)
        if aggregate_node.function == "exists":
            return sqlalchemy.literal(True).label(output_column)
        else:
            # The aggregate node function is a string corresponding to an available
            # sqlalchemy function (e.g. "exists", "count")
            function = getattr(sqlalchemy.func, aggregate_node.function)
            source_column = aggregate_node.column
            return function(query.selected_columns[source_column]).label(output_column)

    def apply_filter(self, query, filter_node):
        # Get the base table
        table_expr = get_primary_table(query)

        column_name = filter_node.column
        operator_name = filter_node.operator
        # Does this filter require another table? i.e. is the filter value itself an
        # Output node, which has a source that we may need to include here
        value_expr, other_tables = self.get_value_expression(filter_node.value)
        if other_tables:
            assert len(other_tables) == 1
            other_table = other_tables[0]
            # If we have a "Value" (i.e. a single value per patient) then we
            # include the other table in the join
            if isinstance(filter_node.value, Value):
                query = self.include_joined_table(query, other_table)
            # If we have a "Column" (i.e. multiple values per patient) then we
            # can't directly join this with our single-value-per-patient query,
            # so we have to use a correlated subquery
            elif isinstance(filter_node.value, Column):
                value_expr = (
                    sqlalchemy.select(value_expr)
                    .select_from(other_table)
                    .where(other_table.c.patient_id == table_expr.c.patient_id)
                )
            else:
                # Shouldn't get any other type here
                assert False

        if isinstance(filter_node.value, Codelist) and "system" in table_expr.c:
            # Codelist queries must also match on `system` column if it's present
            system_column = table_expr.c["system"]
            value_expr = value_expr.where(system_column == filter_node.value.system)

        column = table_expr.c[column_name]
        method = getattr(column, operator_name)
        query_expr = method(value_expr)

        if filter_node.or_null:
            null_expr = column.__eq__(None)
            query_expr = sqlalchemy.or_(query_expr, null_expr)
        return query.where(query_expr)

    @staticmethod
    def apply_row_selector(query, sort_columns, descending):
        """
        Generate query to apply a row selector by sorting by sort_columns in
        specified direction, and then selecting the first row
        """
        # Get the base table - the first in the FROM clauses
        table_expr = get_primary_table(query)

        # Find all the selected column names
        column_names = [column.name for column in query.selected_columns]

        # Query to select the columns that we need to sort on
        order_columns = [table_expr.c[column] for column in sort_columns]
        # change ordering to descending on all order columns if necessary
        if descending:
            order_columns = [c.desc() for c in order_columns]

        # Number rows sequentially over the order by columns for each patient id
        row_num = (
            sqlalchemy.func.row_number()
            .over(order_by=order_columns, partition_by=table_expr.c.patient_id)
            .label("_row_num")
        )
        # Add the _row_num column and select just the first row
        query = query.add_columns(row_num)
        subquery = query.alias()
        query = sqlalchemy.select([subquery.c[column] for column in column_names])
        query = query.select_from(subquery).where(subquery.c._row_num == 1)
        return query

    @staticmethod
    def include_joined_table(query, table):
        if table.name in [t.name for t in get_joined_tables(query)]:
            return query
        join = sqlalchemy.join(
            query.get_final_froms()[0],
            table,
            query.selected_columns.patient_id == table.c.patient_id,
            isouter=True,
        )
        return query.select_from(join)

    def generate_results_query(self):
        """Query to generate the final single results table"""
        # `population` is a special-cased boolean column, it doesn't appear
        # itself in the output but it determines what rows are included
        # Build the base results table from the population table
        column_definitions = self.column_definitions.copy()
        population = column_definitions.pop("population")
        results_query = self.get_population_table_query(population)

        # Build big JOIN query which selects the results
        for column_name, output_node in column_definitions.items():
            # For each output column, generate the query that selects it from its interim table(s)
            # For most outputs there will just be a single interim table.  Category outputs
            # may require more than one.
            column, tables = self.get_value_expression(output_node)
            # Then generate the query to join on it
            for table in tables:
                results_query = self.include_joined_table(results_query, table)

            # Add this column to the final selected results
            results_query = results_query.add_columns(column.label(column_name))

        return results_query

    def get_queries(self):
        """Build the list of SQL queries to execute"""
        self.create_codelist_tables()
        self.create_output_group_tables()
        queries = []
        # Create and populate tables containing codelists
        queries.extend(self.codelist_tables_queries)
        # Generate each of the interim output group tables and populate them
        for group, table in self.output_group_tables.items():
            query = self.output_group_tables_queries[group]
            queries.append(self.write_query_to_table(table, query))
        # Add the big query that creates the base population table and its columns,
        # selected from the output group tables
        queries.append(self.generate_results_query())
        return queries

    def write_query_to_table(self, table, query):
        """
        Returns a new query which, when executed, writes the results of `query`
        into `table`
        """
        raise NotImplementedError()

    @contextlib.contextmanager
    def execute_query(self):
        queries = self.get_queries()

        debug = os.environ.get('DEBUG_LOG_SQL', default="0") == "1"
        if debug:
            from sqlalchemy.dialects import postgresql
    
            print()
            print("-" * 50, "execute_queries", "-" * 50)
            for sql in queries:
                try:
                    print("\t", sql.compile(self.engine, compile_kwargs={"literal_binds": True}))
                except:
                    print("-- non-mssql --")
                    print("\t", sql.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
                print()
            print("-" * 50, "end execute_queries", "-" * 50)

        with self.engine.connect() as cursor:
            for query in queries:
                result = cursor.execute(query)
            # We're only interested in the results from the final query
            yield result
            self.post_execute_cleanup(cursor)

    def post_execute_cleanup(self, cursor):
        """
        A no-op by default but subclasses can implement cleanup logic here
        """


def split_list_into_batches(lst, size=None):
    # If no size limit specified yield the whole list in one batch
    if size is None:
        yield lst
    else:
        for i in range(0, len(lst), size):
            yield lst[i : i + size]
