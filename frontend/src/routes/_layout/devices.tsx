import { createFileRoute } from "@tanstack/react-router"
import DevicesPage from "@/features/devices/DevicesPage"

export const Route = createFileRoute("/_layout/devices")({
  component: DevicesPage,
  head: () => ({ meta: [{ title: "设备管理 - 鲜链智控" }] }),
})
