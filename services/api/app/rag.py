import json
import re
import base64
from pathlib import Path

from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

from . import config

# Only ONE model instance loaded into VRAM
model = ChatOllama(
    model=config.CHAT_MODEL,
    base_url=config.OLLAMA_BASE_URL,
    temperature=config.TEMPERATURE,
    num_ctx=config.NUM_CTX,
    num_predict=config.NUM_PREDICT,
    repeat_penalty=config.REPEAT_PENALTY,
)

def parse_docs(docs):
    """Dynamically load images from disk and base64 encode them."""
    images_b64, texts_out = [], []
    for doc in docs:
        if isinstance(doc, dict) and "img_path" in doc:
            try:
                images_b64.append(base64.b64encode(Path(doc["img_path"]).read_bytes()).decode())
            except Exception:
                pass
        else:
            texts_out.append(doc)
    return {"images": images_b64, "texts": texts_out}

def extract_json(text_response):
    """Ensures we only grab the JSON part of the LLM's response."""
    s = text_response.strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        return {"error": "Failed to generate JSON", "raw": text_response}


# ==========================================
# CHAIN 1: THE WEB CHATBOT (Phase 1)
# ==========================================
def build_prompt(kwargs):
    docs_by_type = kwargs["context"]
    user_question = kwargs["question"]
    context_text = "".join([getattr(el, "text", str(el)) + "\n" for el in docs_by_type["texts"]])

    prompt_template = f"""
Answer the question using only the following context (text, tables, and any images below).
Context: {context_text}
Question: {user_question}

Reply in exactly two sections:

## Step-by-Step Guide
Number every step; use the exact paths, buttons, commands, or field names provided in the context.

## Workflow DAG
Visualize the exact action sequence from your guide as a Mermaid flowchart inside a markdown block. 
Use 'graph TD'. 
CRITICAL RULE: DO NOT use double quotes (") anywhere inside the Mermaid graph. Use single quotes instead.

Example Structure:
```mermaid
graph TD
    Start((Start)) --> A[Execute First Action]
    A --> B{{Is there a Condition?}}
    B -- Yes --> C[Execute Alternative Action 1]
    B -- No --> D[Execute Alternative Action 2]
    C --> End((End))
    D --> End((End))
"""
    content = [{"type": "text", "text": prompt_template}]
    for image in docs_by_type["images"]:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
    return ChatPromptTemplate.from_messages([HumanMessage(content=content)])

def build_chain(retriever):
    return (
        {"context": retriever | RunnableLambda(parse_docs), "question": RunnablePassthrough()}
        | RunnableLambda(build_prompt)
        | model
        | StrOutputParser()
    )


# ==========================================
# CHAIN 2: THE DESKTOP PLANNER (Tier 3)
# ==========================================
PLAN_INSTR = (
    "You are a visual agent. Find the SINGLE NEXT STEP to achieve the user's goal based on the manual.\n"
    "Do not repeat the 'LAST ACTION TAKEN'. Move to the next logical step.\n"
    "Return STRICT JSON only:\n"
    "{\n"
    '  "thought": "<Reasoning>",\n'
    '  "instruction": "<Imperative action to take>",\n'
    '  "target_name": "<Label to click>",\n'
    '  "target_bbox_2d": [0,0,0,0] // [ymin, xmin, ymax, xmax] normalized 0-1000\n'
    "}"
)

def build_guide_prompt(kwargs):
    context_text = "".join([getattr(el, "text", str(el)) + "\n" for el in kwargs["context"]["texts"]])
    
    # Safely fetch last_action with a fallback
    last_act = kwargs.get('last_action', 'None')
    
    text = f"{PLAN_INSTR}\n\nMANUAL:\n{context_text}\n\nLAST ACTION TAKEN: {last_act}\n\nUI ELEMENTS:\n{kwargs['ui_hint']}\n\nGOAL: {kwargs['question']}"
    
    content = [
        {"type": "text", "text": text}, 
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{kwargs['screen_b64']}"}}
    ]
    return ChatPromptTemplate.from_messages([HumanMessage(content=content)])

def build_guide_chain(retriever):
    return (
        {
            "context": RunnableLambda(lambda x: x["question"]) | retriever | RunnableLambda(parse_docs), 
            "question": lambda x: x["question"], 
            "screen_b64": lambda x: x["screen_b64"], 
            "ui_hint": lambda x: x["ui_hint"],
            "last_action": lambda x: x.get("last_action", "None")  # <--- THE CRITICAL BUG FIX IS HERE!
        } 
        | RunnableLambda(build_guide_prompt) 
        | model 
        | StrOutputParser() 
        | RunnableLambda(extract_json)
    )


# ==========================================
# CHAIN 3: THE EVALUATOR (Tier 3 - Dual Frame)
# ==========================================
EVAL_INSTR = (
    "You are a strict Contrastive Evaluator verifying if a software GOAL was achieved.\n"
    "CRITICAL RULE 1: If the 'LAST ACTION TAKEN' is 'None', the goal CANNOT be complete.\n"
    "CRITICAL RULE 2: A pre-existing element is not a success. The goal is only achieved if the LAST ACTION caused the success.\n"
    "Return STRICT JSON only:\n"
    "{\n"
    '  "visual_delta": "<Describe exactly what changed between Image 1 and Image 2>",\n'
    '  "is_complete": true/false,\n'
    '  "reasoning": "<Explain why>"\n'
    "}"
)

def build_eval_prompt(kwargs):
    last_act = kwargs.get('last_action', 'None')
    text = f"{EVAL_INSTR}\n\nGOAL: {kwargs['question']}\nLAST ACTION TAKEN: {last_act}"
    content = [{"type": "text", "text": text}]
    
    # Dual-Frame Image Injection
    if kwargs.get('previous_b64'):
        content.append({"type": "text", "text": "IMAGE 1: BEFORE ACTION"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{kwargs['previous_b64']}"}})
        content.append({"type": "text", "text": "IMAGE 2: AFTER ACTION (CURRENT STATE)"})
    else:
        content.append({"type": "text", "text": "IMAGE 1: CURRENT STATE"})
        
    # Always append the current frame
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{kwargs['screen_b64']}"}})
    
    return ChatPromptTemplate.from_messages([HumanMessage(content=content)])

def build_evaluator_chain():
    return (
        RunnableLambda(build_eval_prompt) 
        | model 
        | StrOutputParser() 
        | RunnableLambda(extract_json)
    )