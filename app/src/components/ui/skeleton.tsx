import * as React from "react"

import { cn } from "@/lib/utils"

// Official shadcn Skeleton primitive, added to the existing Radix Nova UI registry.
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

export { Skeleton }
