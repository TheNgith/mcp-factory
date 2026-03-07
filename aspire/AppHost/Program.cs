// MCP Factory – .NET Aspire App Host
// Orchestrates the pipeline (FastAPI) and web UI containers for local development.
//
// Prerequisites:
//   dotnet workload install aspire
//   Docker Desktop running
//
// One-time secret setup (run from aspire/AppHost/):
//   dotnet user-secrets set "AZURE_OPENAI_ENDPOINT"                 "https://mcp-factory-openai.openai.azure.com/"
//   dotnet user-secrets set "AZURE_OPENAI_DEPLOYMENT"               "gpt-4o"
//   dotnet user-secrets set "AZURE_STORAGE_ACCOUNT"                 "mcpfactorystore"
//   dotnet user-secrets set "APPLICATIONINSIGHTS_CONNECTION_STRING" "<connection-string-from-portal>"
//
// Auth: locally DefaultAzureCredential uses `az login` / IDE credentials.
// AZURE_CLIENT_ID (Managed Identity) is only needed inside Azure Container Apps.
//
// Run:
//   cd aspire/AppHost
//   dotnet run
//
// Aspire dashboard → http://localhost:15000
// Pipeline API     → http://localhost:8000
// Web UI           → http://localhost:8080

using Aspire.Hosting;

var builder = DistributedApplication.CreateBuilder(args);

// ── Parameters / secrets ──────────────────────────────────────────────────
var openaiEndpoint   = builder.AddParameter("AZURE_OPENAI_ENDPOINT",                 secret: true);
var openaiDeployment = builder.AddParameter("AZURE_OPENAI_DEPLOYMENT",               secret: false);
var storageAccount   = builder.AddParameter("AZURE_STORAGE_ACCOUNT",                 secret: false);
var appInsightsConn  = builder.AddParameter("APPLICATIONINSIGHTS_CONNECTION_STRING", secret: true);

// ── Pipeline container (api/main.py via Dockerfile) ───────────────────────
// Dockerfile is at the repo root; listens on port 8000.
var pipeline = builder
    .AddDockerfile(
        name: "mcp-factory-pipeline",
        contextPath: "../..",           // repo root, relative to AppHost/
        dockerfilePath: "../../Dockerfile"
    )
    .WithEnvironment("AZURE_OPENAI_ENDPOINT",                 openaiEndpoint)
    .WithEnvironment("AZURE_OPENAI_DEPLOYMENT",               openaiDeployment)
    .WithEnvironment("AZURE_STORAGE_ACCOUNT",                 storageAccount)
    .WithEnvironment("APPLICATIONINSIGHTS_CONNECTION_STRING", appInsightsConn)
    .WithHttpEndpoint(port: 8000, targetPort: 8000, name: "http");

// ── UI container (ui/main.py via Dockerfile.ui) ───────────────────────────
// Dockerfile.ui listens on port 8080.
// PIPELINE_URL is resolved at runtime to the pipeline container's local address
// so the UI always routes to the correct Aspire-managed endpoint.
builder
    .AddDockerfile(
        name: "mcp-factory-ui",
        contextPath: "../..",
        dockerfilePath: "../../Dockerfile.ui"
    )
    .WithEnvironment("PIPELINE_URL", pipeline.GetEndpoint("http"))
    .WithHttpEndpoint(port: 8080, targetPort: 8080, name: "http");

builder.Build().Run();
