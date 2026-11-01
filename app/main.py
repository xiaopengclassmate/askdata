"""FastAPI 入口。

接口：
  GET    /health                库是否就绪（表行数）+ 今日剩余额度
  GET    /api/schema            渲染给模型看的 schema 文本
  POST   /api/query             裸 SQL 执行（过了安全网关）——调试和压测沙箱用
  POST   /api/ask               Text2SQL 主链路（带会话上下文，问题含糊时返回反问）
  GET    /api/session/{id}      查看该会话上文
  DELETE /api/session/{id}      清空会话

公网保护（/api/ask 会真花 API 额度，这三层是必须的）：
  每 IP 限流 → 全局日配额 → 并发闸门，详见 app/ratelimit.py

启动： uvicorn app.main:app --reload --port 8130
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, dialect, permissions, ratelimit, session, sql_guard, text2sql
from .config import settings
from .logging_setup import one_line, setup_logging
from .ratelimit import LimitHit
from .schema_catalog import render_few_shot, render_schema
from .semantic import render_prompt_block

log = setup_logging(settings.log_level)

app = FastAPI(title="AskData · 自然语言数据分析", version="0.3.0")


class QueryIn(BaseModel):
    sql: str = Field(..., description="待执行的 SQL")
    timeout: float | None = None


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, description="自然语言问题")
    session_id: str | None = Field(None, description="多轮会话 ID；不传则新开一个")
    reset: bool = Field(False, description="清空该会话上文")


def _too_many(exc: LimitHit) -> HTTPException:
    """限流统一转成 429 + Retry-After，让客户端知道等多久，而不是盲目重试。"""
    return HTTPException(429, exc.reason, headers={"Retry-After": str(exc.retry_after)})


def _scope(request: Request):
    """从请求头取身份，解析成行级权限。

    真实系统这里应该是登录态里的用户 → 组织架构 → 可见门店；
    demo 用三个预设身份，不传就是总部（看全部）。
    """
    role = request.headers.get("x-demo-role", "")
    try:
        return permissions.resolve(role)
    except permissions.ScopeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.middleware("http")
async def access_log(request: Request, call_next):
    """访问日志 + 未捕获异常堆栈。静态资源不记，否则一次页面加载刷几十行。"""
    path = request.url.path
    if not (path.startswith("/api/") or path == "/health"):
        return await call_next(request)

    started = time.perf_counter()
    ip = ratelimit.client_ip(request)
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "http status=500 method=%s path=%s ip=%s elapsed_ms=%d",
            request.method,
            path,
            ip,
            int((time.perf_counter() - started) * 1000),
        )
        raise
    log.info(
        "http status=%d method=%s path=%s ip=%s elapsed_ms=%d",
        response.status_code,
        request.method,
        path,
        ip,
        int((time.perf_counter() - started) * 1000),
    )
    return response


@app.get("/health")
def health() -> dict:
    try:
        stats = db.table_stats()
    except Exception as exc:  # 库没建
        raise HTTPException(503, f"数据库不可用：{exc}") from exc
    return {
        "ok": True,
        "llm_enabled": settings.llm_enabled,
        # 当前跑在哪个引擎上：换库之后一眼能确认，不用翻环境变量
        "backend": dialect.engine(),
        "tables": stats,
        # 剩余额度露出来，方便自己一眼看出是不是被刷了（-1 表示不限额）
        "daily_quota_left": ratelimit.limiter.remaining_daily(
            "ask", settings.daily_ask_quota
        ),
    }


@app.get("/api/schema")
def schema() -> dict:
    return {
        "schema": render_schema(),
        "examples": render_few_shot(),
        "semantic": render_prompt_block(),
    }


@app.post("/api/query")
def run_query(payload: QueryIn, request: Request) -> dict:
    # 线上不需要这个口子：它不产生业务价值，却把「随便跑一条 SQL」的能力暴露给公网
    if not settings.enable_raw_sql:
        raise HTTPException(404, "Not Found")

    try:
        ratelimit.limiter.hit(
            ratelimit.client_ip(request), "query", settings.query_rate_per_min
        )
    except LimitHit as exc:
        raise _too_many(exc) from exc

    scope = _scope(request)
    guard = sql_guard.check(payload.sql)
    if not guard:
        raise HTTPException(400, guard.reason)
    try:
        result = db.execute_query(guard.sql, payload.timeout, scope=scope)
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
def ask(payload: AskIn, request: Request) -> dict:
    if not settings.llm_enabled:
        raise HTTPException(503, "未配置 DASHSCOPE_API_KEY，先按 .env.example 建 .env")

    ip = ratelimit.client_ip(request)
    scope = _scope(request)
    sid = payload.session_id or session.store.new_id()

    # ① 单 IP 频率
    try:
        ratelimit.limiter.hit(ip, "ask", settings.ask_rate_per_min)
    except LimitHit as exc:
        log.warning("ask limited=ip ip=%s reason=%s", ip, exc.reason)
        raise _too_many(exc) from exc

    # ② 并发闸门：满了直接拒，不排队（排队只会把内存堆高）
    try:
        with ratelimit.ask_gate:
            # ③ 全局日配额：反问虽然不调模型，也照样占名额——宁可少给，不冒超支的险。
            #    放在闸门里面，被并发拒掉的请求就不会白白烧掉日额度。
            try:
                used = ratelimit.limiter.consume_daily(
                    "ask", settings.daily_ask_quota
                )
            except LimitHit as exc:
                log.warning("ask limited=daily_quota ip=%s reason=%s", ip, exc.reason)
                raise _too_many(exc) from exc

            if payload.reset:
                session.store.clear(sid)

            result = text2sql.answer(
                payload.question,
                history=session.render_history(session.store.history(sid)),
                scope=scope,
            )
    except LimitHit as exc:  # 走到这里只可能是并发闸门（日额度已在里面转成 429）
        log.warning("ask limited=concurrency ip=%s reason=%s", ip, exc.reason)
        raise _too_many(exc) from exc

    result["session_id"] = sid
    result["scope"] = scope.as_dict()  # 前端据此告诉用户「你看到的是哪部分数据」

    # 问题太含糊：正常返回反问，让前端提示用户补充，不是报错
    if result.get("need_clarify"):
        log.info(
            'ask clarify kind=%s session=%s ip=%s q="%s"',
            result.get("clarify_kind"),
            sid,
            ip,
            one_line(payload.question, 80),
        )
        return result

    if not result.get("ok"):
        log.error(
            'ask ok=False session=%s ip=%s attempts=%s elapsed_ms=%s error="%s" '
            'q="%s" retry_log="%s"',
            sid,
            ip,
            result.get("attempts"),
            result.get("elapsed_ms"),
            one_line(result.get("error", "")),
            one_line(payload.question, 80),
            one_line(" | ".join(result.get("retry_log") or []), 400),
        )
        raise HTTPException(422, result.get("error", "生成失败"))

    session.store.append(
        sid, payload.question, result["sql"], result.get("explanation", "")
    )
    # 一行一条：出问题时空手进容器也能知道刚才问了什么、生成什么 SQL、卡在哪
    log.info(
        'ask ok=True quota_used=%s/%s session=%s ip=%s attempts=%s rows=%s '
        'elapsed_ms=%s query_ms=%s warn="%s" q="%s" sql="%s"',
        used,
        settings.daily_ask_quota or "∞",
        sid,
        ip,
        result.get("attempts"),
        result.get("row_count"),
        result.get("elapsed_ms"),
        result.get("query_ms"),
        one_line(result.get("warning") or "-"),
        one_line(payload.question, 80),
        one_line(result.get("sql")),
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
