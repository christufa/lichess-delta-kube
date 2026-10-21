import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { configureApiClient, createQueryClient } from "@/api/client";
import { makeRouter } from "@/router";
import "./index.css";

configureApiClient();
const queryClient = createQueryClient();
const router = makeRouter(queryClient);

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

// Back from Microsoft sign-in (the chat, #69): let MSAL take the response out of the URL before
// the router sees it, and reopen the chat. Everyone else never loads the sign-in code.
if (/[#&](code|error)=/.test(window.location.hash)) {
  try {
    const { completeSignIn, isSignInResponse } = await import("@/chat/auth");
    if (isSignInResponse()) {
      await completeSignIn();
      sessionStorage.setItem("chat-open", "1");
    }
  } catch (e) {
    console.error("sign-in failed", e);
  }
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
