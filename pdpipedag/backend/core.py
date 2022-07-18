import datetime
import hashlib
import itertools
import json
import uuid
from threading import Lock

import pdpipedag
from pdpipedag import backend
from pdpipedag._typing import Materialisable
from pdpipedag.core import schema, materialise, Table
from pdpipedag.core.metadata import TaskMetadata
from pdpipedag.core.util import deepmutate
from pdpipedag.errors import SchemaError


class PipeDAGStore:
    """Main storage interface for materialising tasks."""

    def __init__(
            self,
            table: 'backend.table.BaseTableStore',
            blob: 'backend.blob.BaseBlobStore',
            lock: 'backend.lock.BaseLockManager',
    ):

        self.table_store = table
        self.blob_store = blob
        self.lock_manager = lock

        self.__lock = Lock()
        self.schemas: dict[str, schema.Schema] = {}
        self.swapped_schemas: set[str] = set()
        self.run_id = uuid.uuid4().hex[:20]
        self.json_encoder = json.JSONEncoder(
            ensure_ascii = False,
            allow_nan = False,
            separators = (',', ':'),
            sort_keys = True,
            default = _json_default,
        )

    #### Schema ####

    def create_schema(self, schema: schema.Schema):
        with self.__lock:
            if schema.name in self.schemas:
                raise SchemaError(f"Schema with name '{schema.name}' already exists.")
            if schema.working_name in self.schemas:
                raise SchemaError(f"Schema with working name '{schema.working_name}' already exists.")

            self.schemas[schema.name] = schema
            self.schemas[schema.working_name] = schema

        self.table_store.create_schema(schema)
        # self.blob_store.create_schema(schema)

    def swap_schema(self, schema: schema.Schema):
        """Swap the working schema with the base schema."""
        with self.__lock:
            if schema.name in self.swapped_schemas:
                raise SchemaError(f"Schema with name '{schema.name}' has already been swapped.")
            self.swapped_schemas.add(schema.name)

        with schema.perform_swap():
            self.table_store.swap_schema(schema)
            # self.blob_store.swap_schema(schema)

    #### Task ####

    def dematerialise_task_inputs(
            self,
            task: materialise.MaterialisingTask,
            args: tuple[Materialisable],
            kwargs: dict[str, Materialisable],
    ) -> tuple[tuple, dict]:

        def dematerialise_mutator(x):
            if isinstance(x, Table):
                y = self.table_store.retrieve_table_obj(x, as_type = task.input_type)
                return y
            return x

        d_args = deepmutate(args, dematerialise_mutator)
        d_kwargs = deepmutate(kwargs, dematerialise_mutator)

        return d_args, d_kwargs

    def materialise_task(
            self,
            task: materialise.MaterialisingTask,
            value: Materialisable,
    ):
        schema = task.schema
        assert schema.name in self.schemas

        def materialise_mutator(x, tbl_id = itertools.count()):
            if isinstance(x, Table):
                x.schema = schema
                x.name = f'{task.original_name}_{next(tbl_id):04d}_{task.cache_key}'
                x.cache_key = task.cache_key
                self.table_store.store_table(x, lazy = task.lazy)
            return x

        # Materialise
        m_value = deepmutate(value, materialise_mutator)

        # Metadata?
        output_json = self.json_serialise(m_value)
        metadata = TaskMetadata(
            name = task.original_name,
            schema = schema.name,
            version = task.version,
            timestamp = datetime.datetime.now(),
            run_id = self.run_id,
            cache_key = task.cache_key,
            output_json = output_json,
        )
        self.table_store.store_task_metadata(metadata)

        return m_value

    #### Cache ####

    def compute_task_cache_key(
            self,
            task: materialise.MaterialisingTask,
            input_json: str,
    ) -> str:
        """Compute the cache key for a task.

        This task hash is based on the following values:
        - Task Name
        - Task Version
        - Inputs

        :param task: The task.
        :param input_json: The inputs provided to the task serialized as a json.
        :return: A sha256 hex digest.
        """

        # Maybe look into `dask.base.tokenize`

        v = (
            'PYDIVERSE-PIPEDAG-TASK',
            task.original_name,
            task.version or 'None',
            input_json
        )

        v_str = '|'.join(v)
        v_bytes = v_str.encode('utf8')

        v_hash = hashlib.sha256(v_bytes)
        return v_hash.hexdigest()[:20]  # Provides 40 bit of collision resistance

    def retrieve_cached_output(
            self,
            task: materialise.MaterialisingTask,
    ) -> Materialisable:

        with self.__lock:
            if task.schema.name in self.swapped_schemas:
                raise SchemaError(f"Schema already swapped.")

        metadata = self.table_store.retrieve_task_metadata(task, task.cache_key)
        output = self.json_decode(metadata.output_json)

        return output

    def copy_cached_output_to_working_schema(
            self,
            output: Materialisable,
            task: materialise.MaterialisingTask,
    ):

        def visiting_mutator(x):
            if isinstance(x, Table):
                self.table_store.copy_table_to_working_schema(x)
            return x

        deepmutate(output, visiting_mutator)
        self.table_store.copy_task_metadata_to_working_schema(task)

    #### Utils ####

    def json_serialise(self, value: Materialisable) -> str:
        return self.json_encoder.encode(value)

    def json_decode(self, value: str) -> Materialisable:
        return json.loads(value, object_hook = _json_object_hook)

    def _reset(self):
        self.schemas.clear()
        self.swapped_schemas.clear()
        self.run_id = uuid.uuid4().hex[:20]

PIPEDAG_TYPE = '_pipedag_type_'
PIPEDAG_TYPE_TABLE = 'table'
PIPEDAG_TYPE_BLOB = 'blob'

def _json_default(o):
    if isinstance(o, Table):
        return {
            PIPEDAG_TYPE: PIPEDAG_TYPE_TABLE,
            'schema': o.schema.name,
            'name': o.name,
            'cache_key': o.cache_key,
        }

    raise TypeError(f'Object of type {type(o).__name__} is not JSON serializable')

def _json_object_hook(d: dict):
    pipedag_type = d.get(PIPEDAG_TYPE)
    if pipedag_type:
        if pipedag_type == PIPEDAG_TYPE_TABLE:
            name = d['name']
            schema_name = d['schema']
            cache_key = d['cache_key']

            schema = pdpipedag.config.store.schemas[schema_name]

            return Table(
                name = name,
                schema = schema,
                cache_key = cache_key,
            )

        elif pipedag_type == PIPEDAG_TYPE_BLOB:
            raise NotImplementedError

        else:
            raise ValueError(f"Invalid value for '{PIPEDAG_TYPE}' key: {repr(pipedag_type)}")

    return d
