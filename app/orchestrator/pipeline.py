# app/orchestrator/pipeline.py

from app.orchestrator.state import PipelineState
from app.orchestrator.executor import AgentExecutor

from app.agents.researcher.agent import run_researcher
from app.agents.contact_finder.agent import run_contact_finder
from app.agents.outreach_writer.agent import run_outreach_writer


class OutreachPipeline:
    """
    Runs the full outreach pipeline in three sequential steps:
    Researcher → Contact Finder → Outreach Writer.

    Each step reads from and writes to PipelineState — no agent talks to another directly.
    A failed or skipped step doesn't crash the pipeline, it just influences what the next step does.
    Check state.errors and state.trace after run() to understand exactly what happened.
    """

    def __init__(self):
        # Single executor shared across all steps — handles retries and timeouts uniformly
        self.executor = AgentExecutor()

    async def run(self, state: PipelineState) -> PipelineState:
        """
        Entry point for the pipeline. Takes the initial state, runs all three agents,
        and returns the fully populated state — whatever we managed to fill in.
        """

        # STEP 1: RESEARCHER
        # Goal: turn raw input into a structured BusinessProfile
        # Everything downstream depends on this. If it fails or scores
        # low confidence, the rest of the pipeline degrades gracefully.

        research_result = await self.executor.run(
            "researcher",
            run_researcher,
            state.input
        )

        if research_result.success and research_result.result:
            state.research = research_result.result

            state.trace.append({
                "step": "researcher",
                "status": "success",
                "confidence": state.research.confidence_score,
                "latency_ms": getattr(research_result, "latency", None)  # populated once executor tracks it
            })

            # Low confidence means the profile exists but shouldn't be fully trusted.
            # We don't abort — we flag it and let downstream agents decide what to do with weak data.
            if state.research.confidence_score < 0.3:
                state.trace.append({
                    "step": "researcher",
                    "status": "low_confidence",
                    "action": "no_retry_yet"
                    # Future hook: swap this for self._retry_research(state).
                })

        else:
            # Researcher failed entirely — nothing to pass forward
            state.research = None
            state.errors.append(research_result.error or "research failed")
            state.trace.append({
                "step": "researcher",
                "status": "failed",
                "error": research_result.error
            })

        # STEP 2: CONTACT FINDER
        # Goal: find phone, email, or whatsapp for the business
        # Skipped entirely if researcher came back empty — no point
        # searching for contacts when we don't even know the business.

        if not state.research:
            # Hard skip — no research data means no basis for contact lookup
            state.contact = None
            state.trace.append({
                "step": "contact_finder",
                "status": "skipped",
                "reason": "no research data"
            })

        else:
            contact_result = await self.executor.run(
                "contact_finder",
                run_contact_finder,
                state.research
            )

            if contact_result.success and contact_result.result:
                state.contact = contact_result.result

                state.trace.append({
                    "step": "contact_finder",
                    "status": state.contact.status,
                    "latency_ms": getattr(contact_result, "latency", None)
                })

                # NOT_FOUND is a valid outcome, not a failure — the agent ran fine,
                # we just couldn't find contact info. Outreach will handle this with a fallback.
                if state.contact.status == "NOT_FOUND":
                    state.trace.append({
                        "step": "contact_finder",
                        "status": "not_found",
                        "action": "fallback_outreach"  # outreach writer will receive None for contact
                    })

            else:
                # Agent itself failed — different from NOT_FOUND
                state.contact = None
                state.errors.append(contact_result.error or "contact finder failed")
                state.trace.append({
                    "step": "contact_finder",
                    "status": "failed",
                    "error": contact_result.error
                })

        # STEP 3: OUTREACH WRITER
        # Goal: generate a personalized outreach message
        # Can run in two modes:
        #   - Full mode: research + contact both available
        #   - Fallback mode: only research available, contact is None
        #     (less personalized but still worth sending)
        # Skipped only if research itself is missing.


        if not state.research:
            # Nothing to base a message on — skip entirely
            state.outreach = None
            state.trace.append({
                "step": "outreach_writer",
                "status": "skipped",
                "reason": "no research data"
            })

        else:
            
            # Decide which mode to run in based on contact availability
            no_contact = not state.contact or state.contact.status == "NOT_FOUND"

            outreach_result = await self.executor.run(
                "outreach_writer",
                run_outreach_writer,
                state.research,
                None if no_contact else state.contact  # writer handles None gracefully
            )

            if outreach_result.success and outreach_result.result:
                state.outreach = outreach_result.result
                state.trace.append({
                    "step": "outreach_writer",
                    "status": "success",
                    "latency_ms": getattr(outreach_result, "latency", None)
                })

            else:
                state.outreach = None
                state.errors.append(outreach_result.error or "outreach failed")
                state.trace.append({
                    "step": "outreach_writer",
                    "status": "failed",
                    "error": outreach_result.error
                })

        # ============================================================
        # PIPELINE COMPLETE
        # Append a final summary trace entry so you can tell at a glance
        # whether the pipeline finished cleanly or with errors —
        # without having to scan the entire trace manually.
        # ============================================================
        state.trace.append({
            "step": "pipeline",
            "status": "completed",
            "total_errors": len(state.errors)
        })

        return state