import { createFileRoute } from "@tanstack/react-router"
import OverviewPage from "@/features/overview/OverviewPage"

export const Route = createFileRoute("/_layout/")({
  component: OverviewPage,
  head: () => ({
    meta: [
      {
        title: "业务总览 - 鲜链智控",
      },
    ],
  }),
})
