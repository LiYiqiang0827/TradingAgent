# Market review agent policy

- Before changing or running this toolbox, read `toolbox_manifest.json`,
  `AI_CONTRACT.md`, `REPORT_CONTRACT.md`, `TOOLBOX.md`, and `HANDOFF.md` in that order.
- Do not invoke Gemini or build a Gemini audit packet for routine daily or
  weekly market reviews.
- Use deterministic packet validation first. A bounded GLM worker may draft
  prose from `AGENT_PACKET.json`; its output is unverified.
- Codex owns source-conflict resolution, mainline classification, capital
  migration interpretation, candidate-mode correctness, and final acceptance.
- Do not re-search packet market facts. Web research is limited to catalysts,
  announcements, industry events, and final-candidate context that the packet
  does not contain.
- Commission an external independent review only for a material strategy or
  risk-rule change, a consequential real-trade decision, or an unresolved
  source conflict. This is an explicit exceptional task, not a pipeline stage.
