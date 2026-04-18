import os
from dotenv import load_dotenv
from anthropic import Anthropic
from tools import TOOL_HANDLERS,CHILD_TOOLS,PARENT_TOOLS
from compact import micro_compact, compact_history, estimate_tokens, CompactState     
from settings import MODEL,SUBAGENT_SYSTEM,THRESHOLD,SYSTEM,MODES
from permission import PermissionManager
load_dotenv()

client = Anthropic(
    api_key=os.environ.get('ANTHROPIC_API_KEY'),
    base_url=os.environ.get('ANTHROPIC_BASE_URL')
)


def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # fresh context
    for _ in range(30):  # safety limit
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM, messages=sub_messages,
            tools=CHILD_TOOLS, max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                print(f"[sub]> {block.name}: {block.input}")
                print(output[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)[:50000]})
        sub_messages.append({"role": "user", "content": results})
    # Only the final text returns to the parent -- child context is discarded
    return "".join(b.text for b in response.content if hasattr(b, "text")) or "(no summary)"


import copy

# 定义一个数据结构来承载工具执行的结果和状态变更请求
class ToolExecutionResult:
    def __init__(self, result_dict: dict, request_compact: bool = False, compact_focus: str = None):
        self.result_dict = result_dict
        self.request_compact = request_compact
        self.compact_focus = compact_focus

def tool_execute(block, state: CompactState, perms: PermissionManager) -> ToolExecutionResult:
    """
    纯函数式的工具执行器。
    不再修改外部变量，而是返回执行结果以及是否需要压缩的标志。
    """
    tool_name = block.name
    tool_input = block.input or {}
    
    output = ""
    request_compact = False
    compact_focus = None

    # 1. 处理特殊工具：compact
    if tool_name == "compact":
        request_compact = True
        compact_focus = tool_input.get("focus")
        output = "Compressing..."
        print(f"> {tool_name} (focus: {compact_focus}):")

    # 2. 处理特殊工具：task (子代理)
    elif tool_name == "task":
        desc = tool_input.get("description", "subtask")
        prompt = tool_input.get("prompt", "")
        print(f"> task ({desc}): {prompt[:80]}")
        output = run_subagent(prompt)

    # 3. 处理普通工具
    else:
        handler = TOOL_HANDLERS.get(tool_name)
        try:
            if handler:
                output = handler(**tool_input)
            else:
                output = f"Unknown tool: {tool_name}"
        except Exception as e:
            output = f"Error: {e}"
        print(f"> {tool_name}:")

    # 打印输出预览
    print(output[:200])
    
    # 返回标准结果对象
    return ToolExecutionResult(
        result_dict={"type": "tool_result", "tool_use_id": block.id, "content": output},
        request_compact=request_compact,
        compact_focus=compact_focus
    )

def agent_loop(messages: list, state: CompactState, perms: PermissionManager):
    rounds_since_todo = 0
    
    while True:
        # --- 1. 上下文压缩检查 (Pre-flight Check) ---
        messages[:] = micro_compact(messages)
        print("[micro compact]")
        if estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = compact_history(messages, state, client=client, model=MODEL)

        # --- 2. 调用 LLM ---
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=PARENT_TOOLS, max_tokens=8000,
        )    
        messages.append({"role": "assistant", "content": response.content})
        
        # --- 3. 检查终止条件 ---
        if response.stop_reason != "tool_use":
            return

        # --- 4. 执行工具调用 ---
        results = []
        used_todo = False
        manual_compact_triggered = False # 用于标记本轮是否触发了手动压缩
        current_compact_focus = None

        for block in response.content:
            if block.type == "tool_use":
                # -- 权限检查 --
                decision = perms.check(block.name, block.input or {})
                
                if decision["behavior"] == "deny":
                    output_text = f"Permission denied: {decision['reason']}"
                    print(f"  [DENIED] {block.name}: {decision['reason']}")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output_text})
                    
                elif decision["behavior"] == "ask":
                    if perms.ask_user(block.name, block.input or {}):
                        # 用户允许，执行工具
                        exec_result = tool_execute(block, state, perms)
                        results.append(exec_result.result_dict)
                        # 检查是否需要压缩
                        if exec_result.request_compact:
                            manual_compact_triggered = True
                            current_compact_focus = exec_result.compact_focus
                    else:
                        output_text = f"Permission denied by user for {block.name}"
                        print(f"  [USER DENIED] {block.name}")
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": output_text})
                        
                else:  # allow (默认允许)
                    exec_result = tool_execute(block, state, perms)
                    results.append(exec_result.result_dict)
                    # 检查是否需要压缩
                    if exec_result.request_compact:
                        manual_compact_triggered = True
                        current_compact_focus = exec_result.compact_focus

                # 统计 todo 使用情况
                if block.name == "todo":
                    used_todo = True

        # --- 5. 更新状态与后续逻辑 ---
        
        # 更新 todo 计数器
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
        
        # 将工具结果加入消息历史
        messages.append({"role": "user", "content": results})

        # --- 6. 处理手动压缩 (Manual Compact) ---
        # 如果本轮执行中触发了 compact 工具，执行压缩并退出循环
        if manual_compact_triggered:
            print("[manual compact]")
            messages[:] = compact_history(messages, state, client=client, model=MODEL, focus=current_compact_focus)
            return

if __name__ == "__main__":
    history = []
    compact_state = CompactState()
    print("Permission modes: default, plan, auto")
    mode_input = input("Mode (default): ").strip().lower() or "default"
    if mode_input not in MODES:
        mode_input = "default"

    perms  = PermissionManager(mode = mode_input)
    print(f"[Permission mode: {mode_input}]")

    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break    
        if query.strip().lower() in ("q", "exit", ""):
            break

        if query.startswith("/mode"):
            parts = query.split()
            if len(parts) == 2 and parts[1] in MODES:
                perms.mode = parts[1]
                print(f"[Switched to {parts[1]} mode]")
            else:
                print(f"Usage: /mode <{'|'.join(MODES)}>")
            continue

        if query.strip() == "/rules":
            for i, rule in enumerate(perms.rules):
                print(f"  {i}: {rule}")
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history, compact_state, perms)
        response_content = history[-1]['content']
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()