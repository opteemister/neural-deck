import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

const queryClient = new QueryClient();

function Dashboard() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-100">
      <section className="mx-auto max-w-3xl space-y-6">
        <p className="text-sm font-medium uppercase tracking-[0.3em] text-cyan-300">LLM Studio</p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-6xl">Local model control center</h1>
        <p className="max-w-2xl text-lg leading-8 text-slate-300">
          A self-hosted dashboard for launching local LLMs through multiple execution engines.
        </p>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 text-slate-300">
          The application shell is ready. Engine profiles, process supervision, and live logs will arrive in later specs.
        </div>
      </section>
    </main>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
