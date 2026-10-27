import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/app-shell'
import { BenchmarksPage } from '@/pages/benchmarks'
import { ChatPage } from '@/pages/chat'
import { DashboardPage } from '@/pages/dashboard'
import { EnginesPage } from '@/pages/engines'
import { ModelsPage } from '@/pages/models'
import { LogsPage } from '@/pages/logs'
import { ProfilesPage } from '@/pages/profiles'
import { ProxyPage } from '@/pages/proxy'
import { ApiDocsPage } from '@/pages/api-docs'
import { SettingsPage } from '@/pages/settings'
import { StatusPage } from '@/pages/status'
import { StatisticsPage } from '@/pages/statistics'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } } })

export default function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes><Route element={<AppShell />}><Route path="/" element={<DashboardPage />} /><Route path="/engines" element={<EnginesPage />} /><Route path="/models" element={<ModelsPage />} /><Route path="/profiles" element={<ProfilesPage />} /><Route path="/proxy" element={<ProxyPage />} /><Route path="/api-docs" element={<ApiDocsPage />} /><Route path="/chat" element={<ChatPage />} /><Route path="/status" element={<StatusPage />} /><Route path="/statistics" element={<StatisticsPage />} /><Route path="/logs" element={<LogsPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="/benchmarks" element={<BenchmarksPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></BrowserRouter></QueryClientProvider>
}
