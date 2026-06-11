import { createRoot } from 'react-dom/client'
import { StrictMode } from 'react'
import App from './App'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw new Error('#root missing')
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
