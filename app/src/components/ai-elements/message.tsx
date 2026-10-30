"use client"

import { cjk } from "@streamdown/cjk"
import { code } from "@streamdown/code"
import { math } from "@streamdown/math"
import { mermaid } from "@streamdown/mermaid"
import { memo, type ComponentProps } from "react"
import { Streamdown } from "streamdown"
import { cn } from "@/lib/utils"

/*
 * Local AI Elements subset for Ryozan chat text. The upstream registry message component also installs branch and toolbar UI that depends on button-group and tooltip, but GalaPanel only needs the markdown-safe response renderer.
 * Keep this file aligned with the upstream MessageResponse export before expanding the chat surface to full AI SDK UIMessage rendering.
 */

export type MessageResponseProps = ComponentProps<typeof Streamdown>

const streamdownPlugins = { cjk, code, math, mermaid }

export const MessageResponse = memo(
  ({ className, ...props }: MessageResponseProps) => (
    <Streamdown
      className={cn(
        "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className,
      )}
      plugins={streamdownPlugins}
      {...props}
    />
  ),
  (prevProps, nextProps) =>
    prevProps.children === nextProps.children &&
    nextProps.isAnimating === prevProps.isAnimating,
)

MessageResponse.displayName = "MessageResponse"
