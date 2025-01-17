from enum import Enum

import sqlalchemy.types
from sqlalchemy.types import Boolean, Float, Integer, String, Text

__all__ = [
    "Boolean",
    "Date",
    "DateTime",
    "Float",
    "Integer",
    "String",
    "Text",
    "TYPES_BY_NAME",
]

# SQLAlchemy has different coercion rules for custom types vs built-in types. For
# instance, if you compare a date and a string (using the standard types for both)
# SQLAlchemy will decide how the type coercion should be done without ever checking
# whether the current dialect supplies its own date type with its own coercion rules.
# For our use case this is undesirable as we want to be able to control type coercion
# behaviour. In particular we want to make sure that if a date is ever compared to a
# string in MSSQL then the string is formatted in the special unambigious MSSQL format
# rather than ISO.
#
# Fortunately, there is a fairly simple workaround: we can just create a custom type
# which "decorates" the built-in date type but does not actually modify its behaviour in
# any way. SQLAlchemy will always defer to the custom type when working out how to
# coerce expressions. Although we are in a sense "back" to having to use custom types,
# we can use the same custom type across all databases; each dialect can still provide
# its own custom implementation for built-in types, this trick just forces SQLAlchemy to
# actually use them in all circumstances.


class Date(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.types.Date
    cache_ok = True


class DateTime(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.types.DateTime
    cache_ok = True


TYPES_BY_NAME = Enum(
    "TYPES_BY_NAME",
    {
        "boolean": Boolean,
        "date": Date,
        "datetime": DateTime,
        "float": Float,
        "integer": Integer,
        "varchar": Text,
        "code": Text,
    },
)
