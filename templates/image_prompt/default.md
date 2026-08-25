<!--
GroupBrief 固定群聊漫画 Prompt 模板。

模型先返回经过证据约束的结构化编辑稿，程序再把动态内容填入本模板。
最终 image_prompt.txt 只保留以下固定区块，不暴露 topic ID、证据 JSON 或布局字段。

变量：
  {{group_name}}       本次实时群名称
  {{period_start}}     统计开始时间
  {{period_end}}       统计结束时间
  {{message_count}}    消息数
  {{speaker_count}}    发言人数
  {{main_title}}       当天真实主标题
  {{subtitle}}         当天真实副标题
  {{overall_visual}}   固定群聊漫画要求与本次风格
  {{panels}}           连续的【版面1】至【版面N】
  {{text_rules}}       固定文字规则
  {{footer_summary}}   当天真实底部总结
-->
【任务】
生成一张竖版微信群日报漫画信息图。

【群名称】
{{group_name}}

【统计时间】
{{period_start}} ~ {{period_end}}

【数据】
{{message_count}} 条消息
{{speaker_count}} 人发言

【主标题】
{{main_title}}

【副标题】
{{subtitle}}

【整体视觉】
{{overall_visual}}

{{panels}}

【文字规则】
{{text_rules}}

【底部总结】
{{footer_summary}}
