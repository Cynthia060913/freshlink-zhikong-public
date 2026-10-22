import { createFileRoute } from "@tanstack/react-router"
import AlertsPage from "@/features/alerts/AlertsPage"

export const Route = createFileRoute("/_layout/alerts")({
  component: AlertsPage,
  head: () => ({ meta: [{ title: "告警处置 - 鲜链智控" }] }),
})
