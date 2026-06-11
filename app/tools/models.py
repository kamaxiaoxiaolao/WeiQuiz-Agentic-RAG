from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Type, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator


class ToolType(str, Enum):
    """工具类型枚举"""
    LOCAL = "local"
    API = "api"
    MCP = "mcp"
    SKILL = "skill"


class ToolPermission(str, Enum):
    """工具权限枚举"""
    PUBLIC = "public"
    USER = "user"
    ADMIN = "admin"


class ToolCategory(str, Enum):
    """工具分类枚举"""
    SEARCH = "search"
    DATA = "data"
    CALCULATION = "calculation"
    UTILITY = "utility"
    CUSTOM = "custom"


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[Any] | None = None
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed_types = {"string", "integer", "number", "boolean", "array", "object", "any"}
        if v not in allowed_types:
            raise ValueError(f"Invalid parameter type: {v}. Must be one of {allowed_types}")
        return v


class ToolInputSchema(BaseModel):
    """工具输入Schema"""
    type: str = "object"
    properties: dict[str, ToolParameter] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    
    @model_validator(mode='after')
    def check_required_exists(self) -> 'ToolInputSchema':
        for req in self.required:
            if req not in self.properties:
                raise ValueError(f"Required field '{req}' not found in properties")
        return self


class ToolOutputSchema(BaseModel):
    """工具输出Schema"""
    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """工具规格定义 - 支持版本管理和MCP协议"""
    name: str
    version: str = "1.0.0"
    description: str
    tool_type: ToolType = ToolType.LOCAL
    permission: ToolPermission = ToolPermission.USER
    category: ToolCategory = ToolCategory.CUSTOM
    input_schema: ToolInputSchema = Field(default_factory=ToolInputSchema)
    output_schema: ToolOutputSchema = Field(default_factory=ToolOutputSchema)
    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    deprecation_message: str = ""
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r'^[a-z][a-z0-9_]*$', v):
            raise ValueError("Tool name must start with lowercase letter and contain only lowercase letters, numbers, and underscores")
        return v
    
    @field_validator('version')
    @classmethod
    def validate_version(cls, v: str) -> str:
        if not re.match(r'^\d+\.\d+\.\d+$', v):
            raise ValueError("Version must match semver format (X.Y.Z)")
        return v
    
    def to_openai_format(self) -> dict[str, Any]:
        """转换为OpenAI function calling格式"""
        properties = {}
        required = []
        
        for param_name, param in self.input_schema.properties.items():
            prop_type = param.type
            if prop_type == "integer":
                prop_type = "number"
            elif prop_type == "any":
                prop_type = "string"
            
            properties[param_name] = {
                "type": prop_type,
                "description": param.description,
            }
            if param.enum:
                properties[param_name]["enum"] = param.enum
            if param.default is not None:
                properties[param_name]["default"] = param.default
            
            if param.required:
                required.append(param_name)
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
    
    def to_mcp_format(self) -> dict[str, Any]:
        """转换为MCP (Model Context Protocol)格式"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    name: param.model_dump() for name, param in self.input_schema.properties.items()
                },
                "required": self.input_schema.required,
            },
            "output_schema": self.output_schema.model_dump(),
            "metadata": {
                "tool_type": self.tool_type.value,
                "permission": self.permission.value,
                "category": self.category.value,
                "tags": self.tags,
                "deprecated": self.deprecated,
            },
        }


class ToolCallResult(BaseModel):
    """工具调用结果 - 支持详细的调用元数据"""
    tool_name: str
    success: bool
    content: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0
    usage: dict[str, Any] = Field(default_factory=dict)
    
    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        return super().model_dump(*args, **kwargs)


# 工具处理器类型
ToolHandler = Callable[..., ToolCallResult]
AsyncToolHandler = Callable[..., Awaitable[ToolCallResult]]
