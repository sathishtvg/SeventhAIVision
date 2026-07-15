import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ColorModeProvider } from '@/context/ColorMode'
import { initAxiosInterceptors, restoreSession } from '@/store/auth'
import './i18n'
import App from './App'

initAxiosInterceptors()
restoreSession()

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ColorModeProvider>
          <App />
        </ColorModeProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
)
