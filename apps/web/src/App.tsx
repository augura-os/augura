import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/layout/AppLayout";
import { ConsentDialog, useConsentGiven } from "./components/privacy/ConsentDialog";
import { OnboardingDialog } from "./components/onboarding/OnboardingDialog";
import CreativeGraphPage from "./pages/CreativeGraphPage";
import UploadPage from "./pages/UploadPage";
import AssetListPage from "./pages/AssetListPage";
import AssetDetailPage from "./pages/AssetDetailPage";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  const [consentGiven, acceptConsent] = useConsentGiven();
  return (
    <BrowserRouter>
      <ConsentDialog open={!consentGiven} onAccept={acceptConsent} />
      {consentGiven ? <OnboardingDialog /> : null}
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<CreativeGraphPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/assets" element={<AssetListPage />} />
          <Route path="/assets/:id" element={<AssetDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
