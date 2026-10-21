from pydantic import BaseModel, ConfigDict


class SchemaModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
