import * as React from "react"
import { cn } from "@/lib/utils"

// --- SVG Icons ---

export const CheckCircleIcon = (props: React.SVGProps<SVGSVGElement>) => (
  <svg
    {...props}
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="10" stroke="#70c27b" strokeWidth="2" fill="rgba(112,194,123,0.12)" />
    <polyline points="16 9 11 15 8 12" stroke="#70c27b" strokeWidth="2" />
  </svg>
)

export const PaymentMethodIcon = ({ brand = "card", ...props }: { brand?: string } & React.SVGProps<SVGSVGElement>) => {
  if (brand.toLowerCase() === "mastercard") {
    return (
      <svg {...props} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="36" height="24">
        <circle cx="8" cy="12" r="7" fill="#EA001B" />
        <circle cx="16" cy="12" r="7" fill="#F79E1B" fillOpacity="0.8" />
      </svg>
    )
  }
  return (
    <svg {...props} xmlns="http://www.w3.org/2000/svg" width="32" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="text-[#D38A68]">
      <rect width="20" height="14" x="2" y="5" rx="2" stroke="rgba(232,223,207,0.3)" fill="rgba(10,11,10,0.5)" />
      <line x1="2" x2="22" y1="10" y2="10" stroke="#D38A68" />
    </svg>
  )
}

// --- Helper Components ---

export const DashedLine = ({ className }: { className?: string }) => (
  <div
    className={cn("w-full border-t border-dashed border-[#3a301e]", className)}
    aria-hidden="true"
  />
)

export const TicketBarcode = ({ value }: { value: string }) => {
  const hashCode = (s: string) => s.split('').reduce((a, b) => { a = ((a << 5) - a) + b.charCodeAt(0); return a & a }, 0)
  const seed = hashCode(value)
  const random = (s: number) => {
    const x = Math.sin(s) * 10000
    return x - Math.floor(x)
  }

  const bars = Array.from({ length: 56 }).map((_, index) => {
    const rand = random(seed + index)
    const width = rand > 0.65 ? 2.5 : 1.4
    return { width }
  })

  const spacing = 1.6
  const totalWidth = bars.reduce((acc, bar) => acc + bar.width + spacing, 0) - spacing
  const svgWidth = 240
  const svgHeight = 52
  let currentX = (svgWidth - totalWidth) / 2

  return (
    <div className="flex flex-col items-center py-2">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        aria-label={`Barcode for reference ${value}`}
        className="fill-[#e2dac2]/80"
      >
        {bars.map((bar, index) => {
          const x = currentX
          currentX += bar.width + spacing
          return (
            <rect
              key={index}
              x={x}
              y="4"
              width={bar.width}
              height="44"
            />
          )
        })}
      </svg>
      <p className="text-[10px] font-mono uppercase tracking-[0.24em] text-[rgba(226,218,194,0.55)] mt-1.5">{value}</p>
    </div>
  )
}

// --- Main Ticket Component ---

export interface TicketProps extends React.HTMLAttributes<HTMLDivElement> {
  ticketId: string
  amount: number
  date: Date | string
  cardHolder: string
  last4Digits?: string
  barcodeValue: string
  artistName?: string
  title?: string
  subtitle?: string
  paymentBrand?: string
  icon?: React.ReactNode
}

export const AnimatedTicket = React.forwardRef<HTMLDivElement, TicketProps>(
  (
    {
      className,
      ticketId,
      amount,
      date,
      cardHolder,
      last4Digits = "8237",
      barcodeValue,
      artistName,
      title = "You're Booked!",
      subtitle = "Deposit sealed. Chair confirmed.",
      paymentBrand = "card",
      icon,
      ...props
    },
    ref
  ) => {
    const formattedAmount = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
    }).format(amount)

    const dateObj = typeof date === "string" ? new Date(date) : date
    const formattedDate = !isNaN(dateObj.getTime())
      ? new Intl.DateTimeFormat("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }).format(dateObj)
      : String(date)

    return (
      <div
          ref={ref}
          className={cn(
            "relative w-full max-w-sm rounded-2xl font-sans z-10 overflow-hidden",
            "bg-[#191a19] text-[#e2dac2] border border-[#3a301e]",
            "shadow-[0_25px_60px_rgba(0,0,0,0.75),0_0_35px_rgba(211,138,104,0.12)]",
            className
          )}
          {...props}
        >
          {/* Header watermark & status */}
          <div className="p-7 flex flex-col items-center text-center relative border-b border-[#2a2316]">
            {/* Scallop ticket cut-out notches on perforation */}
            <div className="absolute -left-3.5 -bottom-3.5 w-7 h-7 rounded-full bg-[#0b0c0b] border border-[#3a301e] z-20" aria-hidden="true" />
            <div className="absolute -right-3.5 -bottom-3.5 w-7 h-7 rounded-full bg-[#0b0c0b] border border-[#3a301e] z-20" aria-hidden="true" />

            <div className="mb-3">
              {icon || (
                <div className="p-2.5 bg-[#70c27b]/10 rounded-full border border-[#70c27b]/30">
                  <CheckCircleIcon className="w-8 h-8 text-[#70c27b]" />
                </div>
              )}
            </div>

            <div className="inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full border border-[#70c27b] bg-[#70c27b]/10 mb-2">
              <span className="w-1.5 h-1.5 rounded-full bg-[#70c27b]" />
              <span className="font-mono text-[9px] font-bold tracking-[0.16em] uppercase text-[#70c27b]">Paid &amp; Sealed</span>
            </div>

            <h2 className="text-2xl font-black font-['Poppins'] tracking-wide text-[#f4ecdd] uppercase mt-1">
              {title}
            </h2>
            <p className="text-xs text-[rgba(232,223,207,0.6)] font-['Space_Grotesk'] mt-1">
              {subtitle}
            </p>
          </div>

          {/* Ticket Body / Spec Cells */}
          <div className="px-6 py-5 space-y-4">
            <div className="grid grid-cols-2 gap-2.5 text-left bg-[#101110] border border-[#2a2316] rounded-xl p-3">
              <div>
                <p className="text-[8.5px] font-mono font-bold tracking-[0.18em] uppercase text-[rgba(226,218,194,0.5)]">Ref Code</p>
                <p className="font-mono font-bold text-xs text-[#e2dac2] mt-0.5">{ticketId}</p>
              </div>
              <div className="text-right">
                <p className="text-[8.5px] font-mono font-bold tracking-[0.18em] uppercase text-[rgba(226,218,194,0.5)]">Amount</p>
                <p className="font-semibold text-sm text-[#D38A68] mt-0.5">{formattedAmount}</p>
              </div>
              {artistName ? (
                <div className="col-span-2 pt-2 border-t border-[#2a2316]">
                  <p className="text-[8.5px] font-mono font-bold tracking-[0.18em] uppercase text-[rgba(226,218,194,0.5)]">Artist</p>
                  <p className="font-medium text-xs text-[#f4ecdd] mt-0.5">{artistName}</p>
                </div>
              ) : null}
              <div className="col-span-2 pt-2 border-t border-[#2a2316]">
                <p className="text-[8.5px] font-mono font-bold tracking-[0.18em] uppercase text-[rgba(226,218,194,0.5)]">Date &amp; Time</p>
                <p className="font-medium text-xs text-[#e2dac2] mt-0.5">{formattedDate}</p>
              </div>
            </div>

            {/* Payment Method stub */}
            <div className="bg-[#101110] border border-[#2a2316] p-3 rounded-xl flex items-center justify-between">
              <div className="flex items-center gap-3">
                <PaymentMethodIcon brand={paymentBrand} />
                <div>
                  <p className="font-semibold text-xs text-[#f4ecdd]">{cardHolder}</p>
                  <p className="text-[rgba(232,223,207,0.5)] font-mono text-[11px] tracking-wider mt-0.5">Stripe Secure &bull; {last4Digits}</p>
                </div>
              </div>
              <span className="font-mono text-[8.5px] uppercase tracking-wider text-[#70c27b] bg-[#70c27b]/10 border border-[#70c27b]/25 px-2 py-0.5 rounded">
                Cleared
              </span>
            </div>

            {/* Connected collector-pass stub adapted from the 21st Admit One pattern. */}
            <div className="relative -mx-6 -mb-5 mt-5 px-6 pb-5 pt-4 bg-[linear-gradient(90deg,rgba(211,138,104,0.08),rgba(10,11,10,0.28))]">
              <div className="absolute -top-3 -left-3.5 w-6 h-6 rounded-full bg-[#0b0c0b] border border-[#3a301e] z-20" aria-hidden="true" />
              <div className="absolute -top-3 -right-3.5 w-6 h-6 rounded-full bg-[#0b0c0b] border border-[#3a301e] z-20" aria-hidden="true" />
              <DashedLine className="absolute top-0 left-0" />
              <p className="text-[8px] font-mono font-bold tracking-[0.2em] uppercase text-[#8a8577] text-center mb-1">Collector Pass</p>
              <TicketBarcode value={barcodeValue} />

              {/* Hanko seal and motto remain owned by Email Foundry. */}
              <div className="flex items-center justify-between pt-3 border-t border-[#2a2316]/70">
                <div className="flex flex-col text-left">
                  <span className="font-mono text-[8.5px] uppercase tracking-[0.2em] text-[#8a8577]">Ryozan Studio</span>
                  <span className="font-mono text-[8px] uppercase tracking-[0.14em] text-[#6f6a5c] mt-0.5">Ancient as time, modern as tomorrow</span>
                </div>
                <div className="w-9 h-9 rounded-full border border-[#4a4b45] flex items-center justify-center text-[#c9c0ac] text-base select-none shrink-0" aria-label="Ryozan Hanko Seal">
                  &#34382;
                </div>
              </div>
            </div>
          </div>
      </div>
    )
  }
)

AnimatedTicket.displayName = "AnimatedTicket"

export default AnimatedTicket
