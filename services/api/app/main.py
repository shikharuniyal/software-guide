from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .store import build_retriever
from .rag import build_chain, build_guide_chain, build_evaluator_chain
from .agent_memory import get_state_hash, check_success_state, save_success_state, get_cached_action, save_action

app = FastAPI(title="ONGC Manual RAG")
state = {}

def get_chains():
    #lazy loader approach to avoid reloading by loading chains on demand and handling state in memory
    if "guide_chain" not in state:
        print("[SYSTEM] Building AI Chains...")
        retriever = build_retriever()
        state["chain"] = build_chain(retriever)
        state["guide_chain"] = build_guide_chain(retriever)
        state["eval_chain"] = build_evaluator_chain()
    return state["chain"], state["guide_chain"], state["eval_chain"]

class Query(BaseModel):
    question: str

class GuideQuery(BaseModel):
    question: str
    screen_b64: str
    ui_hint: str
    last_action_failed: bool = False
    previous_b64: str | None = None 
    last_action: str = "None"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/query")
def query(q: Query):
    chain, _, _ = get_chains()
    return StreamingResponse(chain.stream(q.question), media_type="text/plain")

@app.post("/admin/reload")
def reload():
    state.clear() #lazy loader will rebuild on next request
    return {"status": "reloaded"}

def _has_acted(last_action: str) -> bool:
    """True only once the user has actually performed a step (not the start state).
    Gates Tier-2/evaluator so the goal can never be declared COMPLETE before the
    user has done anything on this session."""
    la = (last_action or "").strip().lower()
    return la not in ("", "none") and "starting state" not in la


@app.post("/guide")
def guide(q: GuideQuery):
    _, guide_chain, eval_chain = get_chains()
    state_hash = get_state_hash(q.question, q.ui_hint)
    acted = _has_acted(q.last_action)

    # Completion checks only make sense AFTER the user has taken an action.
    if acted:
        # fast success cache
        if check_success_state(state_hash):
            print("[TIER 2] Cache Hit! Goal is complete.")
            return {
                "status": "COMPLETE",
                "thought": "Verified via Database Memory",
                "instruction": "Task completed successfully.",
            }

        #evaluator VLM checks if the action is the valid goal
        print("[TIER 3] Verifying screen state via VLM (dual-frame)...")
        eval_res = eval_chain.invoke({
            "question": q.question,
            "screen_b64": q.screen_b64,
            "previous_b64": q.previous_b64,
            "last_action": q.last_action,
        })
        if eval_res.get("is_complete") is True:
            print("success! saving to memory")
            save_success_state(state_hash, q.question, eval_res.get("reasoning"))
            return {
                "status": "COMPLETE",
                "thought": eval_res.get("reasoning"),
                "instruction": "Task Completed!",
            }
    else:
        print("planning now")

    # PLANNER makes next node
    cached_plan = get_cached_action(state_hash)
    if q.last_action_failed:
        print("[GUARDRAIL] Client UI didnt change")
        cached_plan = None
    if cached_plan:
        print("[TIER 2] Using cached action.")
        return cached_plan

    print("[PLANNER] Generating next step...")
    plan = guide_chain.invoke({
        "question": q.question,
        "screen_b64": q.screen_b64,
        "ui_hint": q.ui_hint,
        "last_action": q.last_action,
    })
    plan["status"] = "IN_PROGRESS"
    if "error" not in plan:
        save_action(state_hash, q.question, plan)
    return plan