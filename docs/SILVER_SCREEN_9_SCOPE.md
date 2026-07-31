# Silver-Screen 9 scope

Silver-Screen 9 is designed to make the complete existing workflow accessible through one production action. It adds persistent project memory, a Production World Graph, shot-level model recommendations, optional semantic contract review, local visual finishing, edit-decision output, and automatic voice/caption finishing where providers are configured.

The `Blockbuster target` profile means the strongest orchestration and quality thresholds available in the application. It does not claim that current generative-video foundation models can reliably reproduce the labor, physical production, visual-effects pipeline, editorial judgment, and delivery quality of a major human-produced feature film.

The current Streamlit implementation is synchronous. Long jobs can be interrupted by hosting limits, but accepted work, provider prediction IDs, project memory, and the shot queue remain durable. A separate worker service is the future requirement for truly unattended feature-length execution after the browser closes.
