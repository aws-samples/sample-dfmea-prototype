#!/usr/bin/env python3
# infra/app.py
import os
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from stacks.foundation_stack import DfmeaFoundationStack
from stacks.auth_stack import DfmeaAuthStack
from stacks.data_stack import DfmeaDataStack
from stacks.agent_stack import DfmeaAgentStack
from stacks.orchestration_stack import DfmeaOrchestrationStack
from stacks.api_stack import DfmeaApiStack
from stacks.frontend_stack import DfmeaFrontendStack
from stacks.neptune_stack import DfmeaNeptuneStack
from stacks.monitoring_stack import DfmeaMonitoringStack
from stacks.search_stack import DfmeaSearchStack
from stacks.knowledge_base_stack import DfmeaKnowledgeBaseStack
from stacks.agentcore_stack import DfmeaAgentCoreStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

foundation    = DfmeaFoundationStack(app, "DfmeaFoundationStack", env=env)
auth          = DfmeaAuthStack(app, "DfmeaAuthStack", foundation=foundation, env=env)
data          = DfmeaDataStack(app, "DfmeaDataStack", foundation=foundation, env=env)
neptune       = DfmeaNeptuneStack(app, "DfmeaNeptuneStack", foundation=foundation, env=env)
agents        = DfmeaAgentStack(app, "DfmeaAgentStack", foundation=foundation, data=data, neptune=neptune, env=env)
orchestration = DfmeaOrchestrationStack(app, "DfmeaOrchestrationStack", foundation=foundation, data=data, agents=agents, portal_url=os.environ.get("PORTAL_URL", ""), env=env)
search        = DfmeaSearchStack(app, "DfmeaSearchStack", foundation=foundation, data=data, env=env)
knowledge_base = DfmeaKnowledgeBaseStack(app, "DfmeaKnowledgeBaseStack", foundation=foundation, data=data, search=search, env=env)
agentcore     = DfmeaAgentCoreStack(app, "DfmeaAgentCoreStack", foundation=foundation, data=data, auth=auth, neptune=neptune, env=env)
api           = DfmeaApiStack(app, "DfmeaApiStack", foundation=foundation, auth=auth, data=data, orchestration=orchestration, search=search, neptune=neptune, env=env)
api.add_dependency(agentcore)
frontend      = DfmeaFrontendStack(app, "DfmeaFrontendStack", env=env)
monitoring    = DfmeaMonitoringStack(
    app, "DfmeaMonitoringStack",
    foundation=foundation, data=data, agents=agents,
    orchestration=orchestration, api=api,
    ops_email=os.environ.get("OPS_EMAIL", ""),
    monthly_budget_usd=float(os.environ.get("MONTHLY_BUDGET_USD", "500")),
    env=env,
)

# CDK NAG: enforce AWS Solutions rule pack on all stacks
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
