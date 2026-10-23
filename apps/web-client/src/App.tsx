import { Link, Route, Routes } from 'react-router-dom';
import { Layout, RequireRole } from './components/Layout';
import Account from './pages/Account';
import Login from './pages/Login';
import Audit from './pages/admin/Audit';
import Consortium from './pages/admin/Consortium';
import Governance from './pages/admin/Governance';
import Users from './pages/admin/Users';
import Home from './pages/public/Home';
import SubmitTip from './pages/public/SubmitTip';
import TrackTip from './pages/public/TrackTip';
import VerdictDetail from './pages/public/VerdictDetail';
import Verdicts from './pages/public/Verdicts';
import Queue from './pages/workspace/Queue';
import Review from './pages/workspace/Review';
import Reviewed from './pages/workspace/Reviewed';

const STAFF = ['official', 'fact_checker', 'observer'] as const;

function NotFound() {
  return (
    <div className="page narrow">
      <h1>Page not found</h1>
      <p><Link to="/">Go to the home page</Link></p>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="verdicts" element={<Verdicts />} />
        <Route path="verdicts/:id" element={<VerdictDetail />} />
        <Route path="submit" element={<SubmitTip />} />
        <Route path="track" element={<TrackTip />} />
        <Route path="track/:ref" element={<TrackTip />} />
        <Route path="login" element={<Login />} />
        <Route path="account" element={<Account />} />

        <Route path="workspace" element={<RequireRole roles={[...STAFF]}><Queue /></RequireRole>} />
        <Route path="workspace/reviewed" element={<RequireRole roles={[...STAFF]}><Reviewed /></RequireRole>} />
        <Route path="workspace/claims/:id" element={<RequireRole roles={[...STAFF]}><Review /></RequireRole>} />

        <Route path="admin/governance" element={<RequireRole roles={['official']}><Governance /></RequireRole>} />
        <Route path="admin/consortium" element={<RequireRole roles={['official']}><Consortium /></RequireRole>} />
        <Route path="admin/users" element={<RequireRole roles={['official']}><Users /></RequireRole>} />
        <Route path="admin/audit" element={<RequireRole roles={['official']}><Audit /></RequireRole>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
