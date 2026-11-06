"""Reviewed bilingual selection guidance and execution budgets for public tools.

Rows are explicit product contracts: description | use | do not use | seconds.
"""
_ROWS = {
'library_search': ('搜索本人保存的资料并返回正文片段。 Search the owner’s saved documents and excerpts.', '查找或列出应用内文档时使用。 Use to find or list saved materials.', '不用于磁盘路径、他人资料或网页搜索。 Never access arbitrary paths, other users or public web search.', 30),
'library_read': ('按资料 ID 分段读取本人原文。 Read an owned document by ID and offset.', '读取已知资料或继续读取片段时使用。 Use to read a known document or continue a page.', '不读取他人或已删除资料，不推断未提取内容。 Never read other owners, deleted files or invent unextracted text.', 30),
'schedule.create': ('生成定时任务确认卡，用户确认后创建。 Propose a personal schedule for user approval.', '建立一次性提醒或自定义只读指令日程。 Use for reminders or custom read-only schedules.', '时间或内容不明确时先追问；确认卡不代表已创建。 Clarify missing time or content; a proposal is not a saved schedule.', 30),
'schedule.list': ('查看本人定时任务及运行次数。 List the current user’s schedules and run counts.', '用户想查看有哪些定时任务时使用。 Use when the user asks about their scheduled tasks.', '不得查询他人任务或把列表当成运行结果。 Never list another user’s tasks or claim execution from a listing.', 30),
'schedule.update': ('生成修改定时任务的确认卡。 Propose changes to an owned schedule for approval.', '用户要求修改明确任务的时间或内容时使用。 Use to propose a time or content change to a specified task.', '目标不唯一时先追问；确认前不得声称已修改。 Clarify ambiguous targets; do not claim changes before approval.', 30),
'schedule.delete': ('生成删除定时任务的确认卡。 Propose deletion of an owned schedule for approval.', '用户要求删除一个明确的任务时使用。 Use when the user asks to delete a specific personal task.', '不得猜测删除目标或绕过确认卡。 Never guess which task to delete or bypass user approval.', 30),
'schedule.pause': ('冻结本人指定的定时任务。 Pause a specified schedule owned by the current user.', '用户明确指定需要冻结的任务时使用。 Use when the user clearly identifies a task to pause.', '目标不明确时先追问；不得冻结他人任务。 Clarify ambiguous targets and never pause another user’s task.', 30),
'schedule.resume': ('恢复本人指定的定时任务。 Resume an owned schedule within the active task cap.', '用户明确指定需要恢复的任务时使用。 Use when the user clearly identifies a task to resume.', '不得绕过活跃任务上限或恢复他人任务。 Never bypass the active schedule cap or resume another user’s task.', 30),
'schedule.run_now': ('立即运行本人启用的定时任务。 Run an active schedule once for the current user.', '用户明确要求立即执行指定任务时使用。 Use when the user explicitly requests an immediate run.', '不得运行冻结或已删除的任务；无绑定不声称渠道送达。 Never run inactive tasks or claim channel delivery without a binding.', 30),

'profile_read': ('读取用户已保存的个人档案。 Read the saved personal profile.', '需要核对已有姓名、城市、偏好等事实时使用。 Use to retrieve known personal facts.', '不要用来修改档案。 Do not save profile changes.', 5),
'profile_save': ('整体保存用户的个人档案内容。 Replace the stored personal profile.', '已取得完整且核对过的档案时使用。 Use with a complete reviewed profile.', '仅修改个别字段时应使用合并更新。 Do not replace a profile for a small patch.', 10),
'profile_update': ('合并字段并保存用户个人档案。 Merge a patch into the saved profile.', '用户补充或修正档案字段时使用。 Use for incremental profile corrections.', '不要用来覆盖完整档案或推断事实。 Do not invent facts or replace all fields.', 10),
'draft_text': ('根据用户的写作请求和档案生成一版草稿。 Draft an email, message or article for the confirm card.', '用户要求写、改、润色或翻译一段文字时使用。 Use when the user asks for a piece of writing.', '不会发送邮件或消息；草稿必须经用户确认。 Never sends anything; drafts need user confirmation.', 90),
'memory_context': ('组合档案和相关事件供对话参考。 Format profile and relevant events as context.', '回答前需要用户已有背景时使用。 Use to assemble relevant personal context.', '不会保存记忆，也不是完整历史导出。 Do not write memory or export full history.', 10),
'memory_write': ('追加用户的事件及摘要记忆。 Append an event with a summary.', '需要保留已发生的重要事件时使用。 Use to retain a completed event.', '不要记录密码或把推测写成事实。 Do not store secrets or unverified claims.', 10),
'memory_search': ('按关键词、标签和线程检索事件记忆。 Search events by keywords, tags and thread.', '需要寻找与当前问题有关的历史事件时使用。 Use to recall matching events.', '不是向量搜索，也不会修改任何记忆。 Do not use for semantic vector search or edits.', 5),
'memory_facts_search': ('查记忆：混合检索当前用户的有效事实，返回 confirmed 标记。 Search current-user facts with confirmation status.', '用户询问以前说过的偏好、计划或事情时使用。 Use to recall personal facts.', 'confirmed=false 只能核实，不得作为用户确认的事实；不能搜索他人记忆。 Never assert pending inferences or search another user.', 7),
'memory_get': ('通过事件编号读取一条记忆记录。 Retrieve one memory event by its identifier.', '已知事件编号且需要查看详情时使用。 Use to inspect an already identified event.', '没有事件编号时应先搜索记忆。 Do not use for broad history search.', 5),
'memory_update': ('更新本人指定事件的摘要内容。 Replace the summary of an owned memory event.', '用户指出某条事件摘要有误时使用。 Use to correct a specific event summary.', '不要用来修改档案事实或新增事件。 Do not edit profile facts or append events.', 10),
'memory_delete': ('删除本人指定的一条事件记忆。 Delete a single owned memory event.', '用户明确要求移除指定事件时使用。 Use for explicit deletion of one event.', '不要用于清空全部记忆或删除档案。 Do not erase all history or profile facts.', 10),
'memory_delete_all': ('删除用户全部事件记录但保留档案。 Delete all episodic events for the user.', '用户要求清除事件历史时使用。 Use when all event history should be removed.', '要同时忘记档案时应使用全部遗忘流程。 Do not use for full profile erasure.', 20),
'memory_card': ('生成可查看和编辑的个人记忆卡片。 Build a memory card of facts and events.', '用户询问记住了什么或准备编辑记忆时使用。 Use to inspect remembered facts and events.', '不会自动更改卡片中的事实和事件。 Do not use to apply memory edits.', 10),
'memory_fact_update': ('通过记忆卡修改指定档案事实。 Update an identified profile fact from the memory card.', '用户纠正城市、偏好等已展示事实时使用。 Use to correct a displayed profile fact.', '事件摘要修改应使用事件编辑工具。 Do not use for episodic summary edits.', 10),
'memory_fact_delete': ('通过记忆卡删除指定档案事实。 Remove an identified fact from the profile.', '用户明确要求忘记某项档案信息时使用。 Use to forget one specified profile fact.', '不会删除事件历史或清空整个档案。 Do not erase events or the entire profile.', 10),
'memory_episode_update': ('编辑记忆卡中的事件并刷新记忆版本。 Edit a card event and update memory revision.', '用户在记忆卡上纠正某条事件时使用。 Use for a correction from an event card.', '不要用来改结构化档案字段。 Do not edit structured profile facts.', 10),
'memory_episode_delete': ('删除记忆卡中的事件并刷新记忆版本。 Delete a card event and update memory revision.', '用户从记忆卡明确删除一条事件时使用。 Use for deletion from an event card.', '不要用来清空所有事实和全部事件。 Do not erase the entire memory collection.', 10),
'memory_forget': ('经明确确认后清除用户档案和事件记忆。 Erase profile and event memory after confirmation.', '用户明确选择全部遗忘并确认时使用。 Use after explicit confirmation to forget everything.', '只纠正或删除单条记忆时不要使用。 Do not use for a single correction or deletion.', 30),
'summarize_turn': ('把一轮对话加入后台摘要任务队列。 Enqueue a conversation turn for background summarization.', '一轮对话结束且需要保留摘要时使用。 Use after a turn worth summarizing.', '不会立即返回摘要，不用于读取历史。 Do not expect an immediate summary or history read.', 10),
'proactive.deliver': ('向本人已绑定的渠道发送一条提醒。 Deliver one reminder to the owner’s bound channel.', '提醒已准备好且收件渠道属于当前用户时使用。 Use for a prepared reminder to a verified owner channel.', '不能向未绑定渠道或其他用户发送。 Do not deliver to unbound channels or other users.', 20),
}


def policy(name, risk):
    row = _ROWS.get(name)
    if row is None:
        return {}
    description, when, avoid, timeout = row
    return dict(description=description, when_to_use=when, when_not_to_use=avoid,
                timeout=timeout, max_retries=1 if risk == 'read' else 0)
