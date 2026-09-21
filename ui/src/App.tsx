import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import Layout from './components/Layout'
import DashboardPage from './pages/DashboardPage'
import ReviewsPage from './pages/ReviewsPage'
import ReviewDetailPage from './pages/ReviewDetailPage'
import FindingsPage from './pages/FindingsPage'
import UploadPage from './pages/UploadPage'
import OntologyGraphPage from './pages/OntologyGraphPage'
import HitlDecisionsPage from './pages/HitlDecisionsPage'

export default function App() {
  return (
    <Authenticator hideSignUp>
      {({ signOut, user }) => (
        <BrowserRouter>
          <Routes>
            <Route element={<Layout user={user} signOut={signOut} />}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/reviews" element={<ReviewsPage />} />
              <Route path="/reviews/:reviewId" element={<ReviewDetailPage />} />
              <Route path="/reviews/:reviewId/findings" element={<FindingsPage />} />
              <Route path="/hitl-decisions" element={<HitlDecisionsPage />} />
              <Route path="/upload" element={<UploadPage />} />
              <Route path="/ontology" element={<OntologyGraphPage />} />
              <Route path="/ontology/:reviewId" element={<OntologyGraphPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      )}
    </Authenticator>
  )
}
