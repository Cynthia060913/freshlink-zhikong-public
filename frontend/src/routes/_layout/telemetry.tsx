import { createFileRoute } from "@tanstack/react-router"
import TelemetryPage from "@/features/telemetry/TelemetryPage"

export const Route = createFileRoute("/_layout/telemetry")({
  component: TelemetryPage,
  head: () => ({ meta: [{ title: "遥测历史 - 鲜链智控" }] }),
})
