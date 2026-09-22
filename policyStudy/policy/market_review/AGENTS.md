# Market review execution policy

- Before changing or running this toolbox, read `toolbox_manifest.json`,
  `NO_AGENT_HANDOFF.md`, `AI_CONTRACT.md`, `REPORT_CONTRACT.md`, `TOOLBOX.md`,
  and `HANDOFF.md` in that order.
- Routine daily and weekly reviews are Codex-direct. Do not invoke GLM, Hermes,
  Gemini, or another external writing/review agent as a default stage.
- Build and deterministically validate the market packet before interpretation.
  Missing data, an absent ranking row, and numeric zero are distinct states.
- Codex owns source-conflict resolution, mainline classification, capital
  migration interpretation, candidate-mode correctness, direct writing, and
  final acceptance.
- Do not re-search validated packet market facts. Web research is limited to
  catalysts, announcements, industry events, and final-candidate context absent
  from the packet.
- Commission an external independent review only for a material strategy or
  risk-rule change, a consequential real-trade decision, an unresolved source
  conflict, or explicitly low confidence. Treat the result as unverified input.
- Generate PDF only from an accepted Markdown report. Compile with XeLaTeX and
  reject overflow, missing-glyph, blank-page, clipping, or overlap defects.
