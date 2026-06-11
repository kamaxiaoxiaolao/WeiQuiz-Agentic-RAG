# app/kb/router.py
"""Knowledge Base API Router - Document management endpoints."""

from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.storage.auth_models import User
from app.storage.kb_models import Document, DocumentVersion, DocumentTag
from app.services.kb_service import KnowledgeBaseService, DocumentNotFoundError, TagNotFoundError
from app.auth.repository import get_db


router = APIRouter(prefix="/kb", tags=["knowledge-base"])


# Pydantic schemas
class DocumentResponse(BaseModel):
    """文档响应模型"""
    id: str
    title: str
    filename: str
    file_type: str
    file_size: int
    status: str
    description: Optional[str]
    metadata_json: dict
    created_by: str
    created_at: str
    updated_at: str
    last_ingested_at: Optional[str]
    chunk_count: int
    token_count: int
    tags: List[Dict[str, str]]

    @classmethod
    def from_model(cls, doc: Document) -> "DocumentResponse":
        return cls(
            id=doc.id,
            title=doc.title,
            filename=doc.filename,
            file_type=doc.file_type,
            file_size=doc.file_size,
            status=doc.status,
            description=doc.description,
            metadata_json=doc.metadata_json,
            created_by=doc.created_by,
            created_at=doc.created_at.isoformat() if doc.created_at else None,
            updated_at=doc.updated_at.isoformat() if doc.updated_at else None,
            last_ingested_at=doc.last_ingested_at.isoformat() if doc.last_ingested_at else None,
            chunk_count=doc.chunk_count,
            token_count=doc.token_count,
            tags=[
                {"id": tag.id, "name": tag.name, "color": tag.color}
                for tag in doc.tags
            ],
        )


class DocumentUpdateRequest(BaseModel):
    """文档更新请求模型"""
    title: Optional[str] = None
    description: Optional[str] = None
    metadata_json: Optional[dict] = None
    status: Optional[str] = None


class DocumentVersionResponse(BaseModel):
    """文档版本响应模型"""
    id: str
    document_id: str
    version_number: int
    version_name: str
    file_size: int
    changelog: Optional[str]
    created_by: str
    created_at: str

    @classmethod
    def from_model(cls, version: DocumentVersion) -> "DocumentVersionResponse":
        return cls(
            id=version.id,
            document_id=version.document_id,
            version_number=version.version_number,
            version_name=version.version_name,
            file_size=version.file_size,
            changelog=version.changelog,
            created_by=version.created_by,
            created_at=version.created_at.isoformat() if version.created_at else None,
        )


class TagResponse(BaseModel):
    """标签响应模型"""
    id: str
    name: str
    color: str
    description: Optional[str]
    created_by: str
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, tag: DocumentTag) -> "TagResponse":
        return cls(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            description=tag.description,
            created_by=tag.created_by,
            created_at=tag.created_at.isoformat() if tag.created_at else None,
            updated_at=tag.updated_at.isoformat() if tag.updated_at else None,
        )


class TagCreateRequest(BaseModel):
    """标签创建请求模型"""
    name: str
    color: Optional[str] = "#1890ff"
    description: Optional[str] = None


class TagUpdateRequest(BaseModel):
    """标签更新请求模型"""
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None


class DocumentStatsResponse(BaseModel):
    """知识库统计响应模型"""
    total_documents: int
    active_documents: int
    archived_documents: int
    total_tags: int
    total_versions: int


# Helper function to get KB service
def get_kb_service(db: Session = Depends(get_db)) -> KnowledgeBaseService:
    return KnowledgeBaseService(db)


@router.get("/documents", response_model=Dict[str, Any])
async def list_documents(
    status: Optional[str] = Query("active", description="文档状态过滤"),
    file_type: Optional[str] = Query(None, description="文件类型过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """分页获取文档列表"""
    documents, total = kb_service.list_documents(
        status=status,
        file_type=file_type,
        page=page,
        page_size=page_size,
    )
    
    return {
        "documents": [DocumentResponse.from_model(doc) for doc in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取文档详情"""
    try:
        doc = kb_service.get_document(doc_id)
        return DocumentResponse.from_model(doc)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/documents/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: str,
    request: DocumentUpdateRequest,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """更新文档信息"""
    try:
        update_data = {}
        if request.title is not None:
            update_data["title"] = request.title
        if request.description is not None:
            update_data["description"] = request.description
        if request.metadata_json is not None:
            update_data["metadata_json"] = request.metadata_json
        if request.status is not None:
            update_data["status"] = request.status
        
        doc = kb_service.update_document(doc_id, **update_data)
        return DocumentResponse.from_model(doc)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """软删除文档"""
    try:
        kb_service.delete_document(doc_id)
        return {"ok": True, "message": "Document deleted successfully"}
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/documents/search", response_model=Dict[str, Any])
async def search_documents(
    keyword: str = Query(..., description="搜索关键词"),
    status: str = Query("active", description="文档状态过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """搜索文档"""
    documents, total = kb_service.search_documents(
        keyword=keyword,
        status=status,
        page=page,
        page_size=page_size,
    )
    
    return {
        "documents": [DocumentResponse.from_model(doc) for doc in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/documents/{doc_id}/versions", response_model=List[DocumentVersionResponse])
async def get_document_versions(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取文档版本列表"""
    try:
        versions = kb_service.get_versions(doc_id)
        return [DocumentVersionResponse.from_model(v) for v in versions]
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/tags", response_model=List[TagResponse])
async def list_tags(
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取所有标签"""
    tags = kb_service.list_tags()
    return [TagResponse.from_model(tag) for tag in tags]


@router.post("/tags", response_model=TagResponse)
async def create_tag(
    request: TagCreateRequest,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """创建标签"""
    tag = kb_service.create_tag(
        name=request.name,
        color=request.color,
        description=request.description,
        created_by=current_user.id,
    )
    return TagResponse.from_model(tag)


@router.get("/tags/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取标签详情"""
    try:
        tag = kb_service.get_tag(tag_id)
        return TagResponse.from_model(tag)
    except TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: str,
    request: TagUpdateRequest,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """更新标签"""
    try:
        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.color is not None:
            update_data["color"] = request.color
        if request.description is not None:
            update_data["description"] = request.description
        
        tag = kb_service.update_tag(tag_id, **update_data)
        return TagResponse.from_model(tag)
    except TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """删除标签"""
    try:
        kb_service.delete_tag(tag_id)
        return {"ok": True, "message": "Tag deleted successfully"}
    except TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/documents/{doc_id}/tags/{tag_id}")
async def add_tag_to_document(
    doc_id: str,
    tag_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """给文档添加标签"""
    try:
        kb_service.add_tag_to_document(doc_id, tag_id)
        return {"ok": True, "message": "Tag added to document successfully"}
    except (DocumentNotFoundError, TagNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/documents/{doc_id}/tags/{tag_id}")
async def remove_tag_from_document(
    doc_id: str,
    tag_id: str,
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """从文档移除标签"""
    kb_service.remove_tag_from_document(doc_id, tag_id)
    return {"ok": True, "message": "Tag removed from document successfully"}


@router.get("/tags/{tag_id}/documents", response_model=Dict[str, Any])
async def get_documents_by_tag(
    tag_id: str,
    status: str = Query("active", description="文档状态过滤"),
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取指定标签的文档"""
    try:
        documents = kb_service.get_documents_by_tag(tag_id, status)
        return {
            "documents": [DocumentResponse.from_model(doc) for doc in documents],
            "total": len(documents),
        }
    except TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/stats", response_model=DocumentStatsResponse)
async def get_kb_stats(
    current_user: User = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    """获取知识库统计信息"""
    stats = kb_service.get_document_stats()
    return DocumentStatsResponse(**stats)