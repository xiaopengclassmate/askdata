"""FastAPI 入口。

接口：
  GET    /health                库是否就绪（表行数）
  GET    /api/schema            渲染给模型看的 schema 文本
  POST   /api/query             裸 SQL 执行（过了安全网关）——调试和压测沙箱用
  POST   /api/ask               Text2SQL 主链路（带会话上下文，问题含糊时返回反问）
  GET    /api/session/{id}      查看该会话上文
  DELETE /api/session/{id}      清空会话

启动： uvicorn app.main:app --reload --port 8130
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, session, sql_guard, text2sql
from .config import settings
from .schema_catalog import render_few_shot, render_schema
from .semantic import render_prompt_block

app = FastAPI(title="AskData · 自然语言数据分析", version="0.2.0")


class QueryIn(BaseModel):
    sql: str = Field(..., description="待执行的 SQL")
    timeout: float | None = None


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, description="自然语言问题")
    session_id: str | None = Field(None, description="多轮会话 ID；不传则新开一个")
    reset: bool = Field(False, description="清空该会话上文")


@app.get("/health")
def health() -> dict:
    try:
        stats = db.table_stats()
    except Exception as exc:  # 库没建
        raise HTTPException(503, f"数据库不可用：{exc}") from exc
    return {
        "ok": True,
        "llm_enabled": settings.llm_enabled,
        "tables": stats,
    }


@app.get("/api/schema")
def schema() -> dict:
    return {
        "schema": render_schema(),
        "examples": render_few_shot(),
        "semantic": render_prompt_block(),
    }


@app.post("/api/query")
def run_query(payload: QueryIn) -> dict:
    guard = sql_guard.check(payload.sql)
    if not guard:
        raise HTTPException(400, guard.reason)
    try:
        result = db.execute_query(guard.sql, payload.timeout)
    except db.QueryTimeout as exc:
        raise HTTPException(408, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"SQL 执行失败：{exc}") from exc
    return {
        "sql": guard.sql,
        "columns": result.columns,
        "rows": result.rows,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
    }


@app.post("/api/ask")
def ask(payload: AskIn) -> dict:
    if not settings.llm_enabled:
        raise HTTPException(503, "未配置 DASHSCOPE_API_KEY，先按 .env.example 建 .env")

    sid = payload.session_id or session.store.new_id()
    if payload.reset:
        session.store.clear(sid)

    result = text2sql.answer(payload.question, history=session.render_history(
        session.store.history(sid)
    ))
    result["session_id"] = sid

    # 问题太含糊：正常返回反问，让前端提示用户补充，不是报错
    if result.get("need_clarify"):
        return result

    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "生成失败"))

    session.store.append(
        sid, payload.question, result["sql"], result.get("explanation", "")
    )
    return result


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> dict:
    return {"session_id": session_id, "turns": session.store.history(session_id)}


@app.delete("/api/session/{session_id}")
def clear_session(session_id: str) -> dict:
    session.store.clear(session_id)
    return {"session_id": session_id, "cleared": True}


# 前端构建产物存在就一起托管（单容器部署用）；不存在时只提供 API
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
