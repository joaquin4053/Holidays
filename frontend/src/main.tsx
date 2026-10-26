import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import Builder from './pages/Builder'
import TripPage from './pages/TripPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Homepage (4a) and trip builder (6a) are one screen; /plan?trip=<id> pre-fills it ("Edit route"). */}
        <Route path="/" element={<Builder />} />
        <Route path="/plan" element={<Builder />} />
        <Route path="/trip/:id" element={<TripPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
