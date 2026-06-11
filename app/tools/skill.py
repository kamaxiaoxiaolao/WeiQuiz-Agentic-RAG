from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Union

from pydantic import BaseModel, Field

from app.tools.models import ToolSpec, ToolCallResult, ToolType, ToolCategory, ToolPermission
from app.tools.registry import ToolRegistry


class SkillStepType(str, Enum):
    """技能步骤类型"""
    TOOL_CALL = "tool_call"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    LOOP = "loop"
    TRANSFORM = "transform"


class SkillStep(BaseModel):
    """技能步骤定义"""
    id: str
    type: SkillStepType
    tool_name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    arguments_expr: Optional[str] = None
    next_step: Optional[str] = None
    then_step: Optional[str] = None
    else_step: Optional[str] = None
    condition: Optional[str] = None
    iterations: Optional[int] = None
    steps: Optional[List['SkillStep']] = None
    transform_expr: Optional[str] = None


class SkillSpec(ToolSpec):
    """技能规格定义 - 继承自ToolSpec"""
    steps: List[SkillStep] = Field(default_factory=list)
    requires_tools: List[str] = Field(default_factory=list)
    
    @classmethod
    def from_tool_spec(cls, tool_spec: ToolSpec, steps: List[SkillStep]) -> 'SkillSpec':
        """从ToolSpec创建SkillSpec"""
        return cls(
            name=tool_spec.name,
            version=tool_spec.version,
            description=tool_spec.description,
            tool_type=ToolType.SKILL,
            permission=tool_spec.permission,
            category=tool_spec.category,
            input_schema=tool_spec.input_schema,
            output_schema=tool_spec.output_schema,
            tags=tool_spec.tags,
            deprecated=tool_spec.deprecated,
            deprecation_message=tool_spec.deprecation_message,
            steps=steps,
            requires_tools=[s.tool_name for s in steps if s.tool_name],
        )


class SkillExecutionContext(BaseModel):
    """技能执行上下文"""
    input_args: Dict[str, Any] = Field(default_factory=dict)
    step_results: Dict[str, ToolCallResult] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    current_step: Optional[str] = None
    is_complete: bool = False
    error: Optional[str] = None


class SkillExecutor:
    """技能执行引擎"""
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
    
    async def execute(
        self,
        spec: SkillSpec,
        arguments: Dict[str, Any],
        user: Optional[Any] = None,
    ) -> ToolCallResult:
        """执行技能"""
        context = SkillExecutionContext(input_args=arguments)
        
        try:
            # 检查依赖工具是否可用
            await self._validate_dependencies(spec, user)
            
            # 执行技能步骤
            first_step = spec.steps[0] if spec.steps else None
            if first_step:
                await self._execute_step(first_step, context, user)
            
            # 汇总结果
            return self._build_final_result(spec.name, context)
        
        except Exception as exc:
            return ToolCallResult(
                tool_name=spec.name,
                success=False,
                error=f"skill_execution_error:{exc}",
                content=str(exc),
            )
    
    async def _validate_dependencies(self, spec: SkillSpec, user: Optional[Any]) -> None:
        """验证依赖工具"""
        for tool_name in spec.requires_tools:
            tool_spec = self.registry.get_spec(tool_name)
            if not tool_spec:
                raise ValueError(f"Required tool not found: {tool_name}")
            if not self.registry._has_permission(tool_spec, user):
                raise ValueError(f"Permission denied for tool: {tool_name}")
    
    async def _execute_step(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
        user: Optional[Any],
    ) -> None:
        """执行单个步骤"""
        context.current_step = step.id
        
        if step.type == SkillStepType.TOOL_CALL:
            await self._execute_tool_call(step, context, user)
        elif step.type == SkillStepType.PARALLEL:
            await self._execute_parallel(step, context, user)
        elif step.type == SkillStepType.CONDITIONAL:
            await self._execute_conditional(step, context, user)
        elif step.type == SkillStepType.LOOP:
            await self._execute_loop(step, context, user)
        elif step.type == SkillStepType.TRANSFORM:
            await self._execute_transform(step, context)
        
        # 执行下一步
        if step.next_step:
            next_step = self._find_step(step.next_step, context)
            if next_step:
                await self._execute_step(next_step, context, user)
    
    async def _execute_tool_call(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
        user: Optional[Any],
    ) -> None:
        """执行工具调用步骤"""
        if not step.tool_name:
            return
        
        args = self._resolve_arguments(step, context)
        result = await self.registry.call_async(step.tool_name, args, user=user)
        context.step_results[step.id] = result
        
        # 保存结果到变量
        if result.success:
            context.variables[f"step_{step.id}_result"] = result.content
            context.variables[f"step_{step.id}_raw"] = result.raw
    
    async def _execute_parallel(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
        user: Optional[Any],
    ) -> None:
        """执行并行步骤"""
        if not step.steps:
            return
        
        tasks = []
        for sub_step in step.steps:
            task = asyncio.create_task(self._execute_step(sub_step, context, user))
            tasks.append(task)
        
        await asyncio.gather(*tasks)
    
    async def _execute_conditional(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
        user: Optional[Any],
    ) -> None:
        """执行条件分支步骤"""
        condition_met = self._evaluate_condition(step.condition, context)
        
        target_step_id = step.then_step if condition_met else step.else_step
        if target_step_id:
            target_step = self._find_step(target_step_id, context)
            if target_step:
                await self._execute_step(target_step, context, user)
    
    async def _execute_loop(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
        user: Optional[Any],
    ) -> None:
        """执行循环步骤"""
        iterations = step.iterations or 5
        
        for _ in range(iterations):
            if step.steps:
                for sub_step in step.steps:
                    await self._execute_step(sub_step, context, user)
            
            # 检查退出条件
            if step.condition and not self._evaluate_condition(step.condition, context):
                break
    
    async def _execute_transform(
        self,
        step: SkillStep,
        context: SkillExecutionContext,
    ) -> None:
        """执行数据转换步骤"""
        if step.transform_expr:
            try:
                result = eval(step.transform_expr, {}, context.variables)
                context.variables[f"step_{step.id}_result"] = result
            except Exception as exc:
                logger.error(f"Transform error: {exc}")
    
    def _resolve_arguments(self, step: SkillStep, context: SkillExecutionContext) -> Dict[str, Any]:
        """解析参数表达式"""
        if step.arguments_expr:
            return self._evaluate_expression(step.arguments_expr, context)
        return step.arguments or {}
    
    def _evaluate_condition(self, condition: Optional[str], context: SkillExecutionContext) -> bool:
        """评估条件表达式"""
        if not condition:
            return False
        try:
            return bool(eval(condition, {}, context.variables))
        except Exception:
            return False
    
    def _evaluate_expression(self, expr: str, context: SkillExecutionContext) -> Any:
        """评估表达式"""
        try:
            return eval(expr, {}, context.variables)
        except Exception as exc:
            logger.error(f"Expression evaluation error: {exc}")
            return {}
    
    def _find_step(self, step_id: str, context: SkillExecutionContext) -> Optional[SkillStep]:
        """查找步骤"""
        # 在实际实现中，需要从技能定义中查找
        return None
    
    def _build_final_result(self, skill_name: str, context: SkillExecutionContext) -> ToolCallResult:
        """构建最终结果"""
        if context.error:
            return ToolCallResult(
                tool_name=skill_name,
                success=False,
                error=context.error,
            )
        
        # 获取最后一个步骤的结果作为技能输出
        last_step_id = list(context.step_results.keys())[-1] if context.step_results else None
        last_result = context.step_results.get(last_step_id)
        
        if last_result:
            return ToolCallResult(
                tool_name=skill_name,
                success=last_result.success,
                content=last_result.content,
                raw={**context.variables, "step_results": {k: v.model_dump() for k, v in context.step_results.items()}},
                error=last_result.error,
            )
        
        return ToolCallResult(
            tool_name=skill_name,
            success=True,
            content=json.dumps(context.variables, ensure_ascii=False),
            raw=context.variables,
        )


class SkillRegistry:
    """技能注册中心"""
    
    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry
        self._skills: Dict[str, SkillSpec] = {}
    
    def register(self, spec: SkillSpec) -> None:
        """注册技能"""
        if spec.name in self._skills:
            raise ValueError(f"Skill already registered: {spec.name}")
        
        # 创建技能处理器
        executor = SkillExecutor(self.tool_registry)
        
        async def handler(arguments: Dict[str, Any], user: Optional[Any] = None) -> ToolCallResult:
            return await executor.execute(spec, arguments, user)
        
        self._skills[spec.name] = spec
        self.tool_registry.register(spec, handler, is_async=True)
    
    def get_skill(self, name: str) -> Optional[SkillSpec]:
        """获取技能规格"""
        return self._skills.get(name)
    
    def list_skills(self) -> List[SkillSpec]:
        """列出所有技能"""
        return list(self._skills.values())


def build_default_skill_registry(tool_registry: ToolRegistry) -> SkillRegistry:
    """构建默认技能注册中心"""
    registry = SkillRegistry(tool_registry)
    return registry


# 示例：创建一个复合搜索技能
def create_compound_search_skill() -> SkillSpec:
    """创建复合搜索技能 - 先搜索知识库，再搜索网页"""
    steps = [
        SkillStep(
            id="kb_search_step",
            type=SkillStepType.TOOL_CALL,
            tool_name="kb_search",
            arguments_expr={"query": "input_args.get('query')"},
            next_step="web_search_step",
        ),
        SkillStep(
            id="web_search_step",
            type=SkillStepType.TOOL_CALL,
            tool_name="web_search",
            arguments_expr={"query": "input_args.get('query')"},
        ),
    ]
    
    return SkillSpec(
        name="compound_search",
        version="1.0.0",
        description="先搜索知识库，再搜索网页，综合返回结果",
        tool_type=ToolType.SKILL,
        permission=ToolPermission.USER,
        category=ToolCategory.SEARCH,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询", "required": True},
            },
            "required": ["query"],
        },
        steps=steps,
        requires_tools=["kb_search", "web_search"],
        tags=["search", "compound", "hybrid"],
    )
