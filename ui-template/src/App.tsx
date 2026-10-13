import { Route, Routes } from "react-router-dom";

import { ConsolePage } from "@/pages/console-page";
import { LandingPage } from "@/pages/landing-page";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/chat" element={<ConsolePage />} />
    </Routes>
  );
}
