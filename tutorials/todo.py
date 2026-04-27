class TodoManager:
    def __init__(self):
        self.items = []
    
    def update(self, items:list) -> str:
        if len(items) > 20:
            raise ValueError("max 20 todos allowed")
        validated = []
        in_process_count = 0
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i+1)))
            if not text:
                raise ValueError(f"item {item_id}: text required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"item {item_id}: invalid status '{status}'")
            if status == "in_progress":
                in_process_count += 1
            validated.append({"id": item_id, "text":text, "status":status})
        if in_process_count > 1:
            raise ValueError("Only one task can be in_progress at a time")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No Todos."
        lines = []
        for item in self.items:
            marker = {"pending":"[ ]", "in_progress":"[>]","completed": "[x]"} [item["status"]]
            lines.append(f"{marker} #({item['id']}): {item['text']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

TODO = TodoManager()
