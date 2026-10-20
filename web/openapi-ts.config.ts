import { defineConfig } from "@hey-api/openapi-ts";

// The API's contract (api/openapi.json, generated from the FastAPI code) → a typed client
// and TanStack Query options in src/api/generated. Committed; CI regenerates it and fails
// if it differs, so the dashboard and the API can't drift apart.
export default defineConfig({
  input: "../api/openapi.json",
  output: { path: "src/api/generated", postProcess: [] },
  plugins: ["@hey-api/client-fetch", "@hey-api/typescript", "@hey-api/sdk", "@tanstack/react-query"],
});
