import { Routes, Route } from "react-router-dom";
import { LandingPage } from "./pages/LandingPage";
import { AppShell } from "./components/layout/AppShell";
import { WorkInProgress } from "./pages/WorkInProgress";
import { IngestionResultPage } from "./pages/IngestionResultPage";
import { InvestigatePage } from "./pages/InvestigatePage";
import { ChangesPage } from "./pages/ChangesPage";
import { ImpactPage } from "./pages/ImpactPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SourcePage } from "./pages/SourcePage";
import { CommandPalette } from "./components/ui/CommandPalette";

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/repo/:id" element={<AppShell />}>
          <Route index element={<IngestionResultPage />} />
          <Route path="investigate" element={<InvestigatePage />} />
          <Route path="changes" element={<ChangesPage />} />
          <Route path="impact" element={<ImpactPage />} />
          <Route path="review" element={<ReviewPage />} />
          <Route path="source" element={<SourcePage />} />
        </Route>
        <Route path="*" element={<WorkInProgress title="Not Found" />} />
      </Routes>
      <CommandPalette />
    </>
  );
}