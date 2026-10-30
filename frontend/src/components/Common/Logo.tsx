import { Link } from "@tanstack/react-router"
import { Snowflake } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content = (
    <span
      role="img"
      aria-label="鲜链智控"
      className={cn("inline-flex items-center gap-2 font-semibold", className)}
    >
      <Snowflake className="size-6 shrink-0 text-primary" aria-hidden="true" />
      {variant !== "icon" && (
        <span
          className={cn(
            "whitespace-nowrap text-xl tracking-tight",
            variant === "responsive" && "group-data-[collapsible=icon]:hidden",
          )}
        >
          鲜链智控
        </span>
      )}
    </span>
  )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
