import unicodedata

from pydantic import Field, field_validator

from app.planning.schemas import Confirmed


def tag_key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


class TagsInput(Confirmed):
    version: int = Field(ge=0)
    tags: list[str] = Field(max_length=20)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values):
        result = []
        seen = set()
        for value in values:
            label = unicodedata.normalize("NFKC", value).strip()
            if not 1 <= len(label) <= 30 or "," in label or any(unicodedata.category(c).startswith("C") for c in label):
                raise ValueError("请用 1 至 30 字的可见文字命名标签，并用逗号分隔各个标签")
            key = tag_key(label)
            if key in seen:
                raise ValueError("标签重复，请合并后保存")
            result.append(label)
            seen.add(key)
        return result


class WorkspaceAction(Confirmed):
    version: int = Field(ge=0)
    action: str = Field(pattern=r"^(create_folder|rename_folder|move_folder|remove_folder|move_source|star_source)$")
    folder_id: str = Field(default="", max_length=32)
    parent_id: str = Field(default="", max_length=32)
    source_id: str = Field(default="", max_length=32)
    name: str = Field(default="", max_length=60)
    starred: bool = False

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("文件夹名称请使用可见文字")
        return value
