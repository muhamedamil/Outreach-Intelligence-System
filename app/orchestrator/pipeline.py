# app/orchestrator/pipeline.py

from app.orchestrator.state import PipelineState
from app.orchestrator.executor import AgentExecutor

from app.agents.researcher.agent import run_researcher
from app.agents.contact_finder.agent import run_contact_finder
from app.agents.outreach_writer.agent import run_outreach_writer

from app.models.contact import ContactCard, ContactStatus


class OutreachPipeline:
    """
    Async, failure-aware, state-driven pipeline:
    Researcher → Contact Finder → Outreach Writer
    """

    def __init__(self):
        self.executor = AgentExecutor()

    async def run(self, state: PipelineState) -> PipelineState:

        # STEP 1: RESEARCHER

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
                "latency_ms": getattr(research_result, "latency", None)
            })

            # Low confidence signal (non-blocking)
            if state.research.confidence_score < 0.3:
                state.trace.append({
                    "step": "researcher",
                    "status": "low_confidence",
                    "action": "no_retry_yet"
                })

        else:
            state.research = None

            state.errors.append(research_result.error or "research failed")

            state.trace.append({
                "step": "researcher",
                "status": "failed",
                "error": research_result.error
            })

        # STEP 2: CONTACT FINDER

        if not state.research:
            # Return consistent object instead of None
            state.contact = ContactCard(
                status=ContactStatus.NOT_FOUND,
                confidence_score=0.0,
                sources=[]
            )

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

                status_value = state.contact.status.value

                state.trace.append({
                    "step": "contact_finder",
                    "status": status_value,
                    "latency_ms": getattr(contact_result, "latency", None)
                })

                # Explicit NOT_FOUND handling
                if status_value == "NOT_FOUND":
                    state.trace.append({
                        "step": "contact_finder",
                        "status": "not_found",
                        "action": "fallback_outreach"
                    })

                # PARTIAL handling (important signal)
                elif status_value == "PARTIAL":
                    state.trace.append({
                        "step": "contact_finder",
                        "status": "partial",
                        "action": "limited_outreach"
                    })

            else:
                # Agent failure → fallback object (NOT None)
                state.contact = ContactCard(
                    status=ContactStatus.NOT_FOUND,
                    confidence_score=0.0,
                    sources=[]
                )

                state.errors.append(contact_result.error or "contact finder failed")

                state.trace.append({
                    "step": "contact_finder",
                    "status": "failed",
                    "error": contact_result.error
                })

        # STEP 3: OUTREACH WRITER

        if not state.research:
            state.outreach = None

            state.trace.append({
                "step": "outreach_writer",
                "status": "skipped",
                "reason": "no research data"
            })

        else:
            contact_status = state.contact.status.value if state.contact else "NOT_FOUND"

            no_contact = contact_status == "NOT_FOUND"

            outreach_result = await self.executor.run(
                "outreach_writer",
                run_outreach_writer,
                state.research,
                None if no_contact else state.contact
            )

            if outreach_result.success and outreach_result.result:
                state.outreach = outreach_result.result

                state.trace.append({
                    "step": "outreach_writer",
                    "status": "success",
                    "mode": "fallback" if no_contact else "full",
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

        # FINAL PIPELINE STATUS

        final_status = "success" if len(state.errors) == 0 else "partial_success"

        state.trace.append({
            "step": "pipeline",
            "status": final_status,
            "total_errors": len(state.errors)
        })

        return state