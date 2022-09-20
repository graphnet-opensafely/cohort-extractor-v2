# Notes for Graphnet Developers

This is the development notes for the Graphnet environment.

> see the original [DEVELOPERS.md](./DEVELOPERS.md) for general notes

## Local debugging

Attach the python debugger to run sample test with the following steps:
1. setup a python working directory `./sample-researchs`
2. download the [sample project](https://github.com/opensafely/test-age-distribution) into the working directory
3. run python debug mode in the module `databuilder` with the following parameters and env variables:
```
generate-dataset --dataset-definition analysis/dataset_definition.py --output output/dataset.csv --dummy-data-file dummy_output/dataset.csv
```
```
PYTHONUNBUFFERED=1;
DATABASE_URL=mssql://{username}:{password}@{sql server name}.database.windows.net:1433/{DB name};
OPENSAFELY_BACKEND=databuilder.backends.graphnet.GraphnetBackend;
DEBUG=0
```

## Graphnet backend development

[graphnet.py](./databuilder/backends/graphnet.py) need to be implemented based on the [data contract](https://docs.google.com/spreadsheets/d/1Fu5cfmoUHGC4CY4OdEn-7rchhzjMMy--luAQJMeYlr4/edit#gid=2100914852)

### Customisation for Synapse

In order to improvement the performance for Synapse, we need to create tables to temporary store the data.
A SQL similar to this should be added to [base_sql.py#L495](https://github.com/opensafely-core/databuilder/blob/main/databuilder/query_engines/base_sql.py#L495)

```
CREATE TABLE TRE.CLINICALEVENTS
WITH (DISTRIBUTION = ROUND_ROBIN, CLUSTERED COLUMNSTORE INDEX ORDER(CONSULTATIONDATE))
AS
SELECT * FROM TRE.[Extl_ClinicalEvents]
```