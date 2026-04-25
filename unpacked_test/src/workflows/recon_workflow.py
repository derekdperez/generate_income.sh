from temporalio import workflow
from datetime import timedelta

@workflow.def_workflow
class ReconWorkflow:
    @workflow.run
    async def run(self, target: str):
        result = await workflow.execute_activity(
            "enumerate_subdomains", target, start_to_close_timeout=timedelta(minutes=10)
        )
        return result