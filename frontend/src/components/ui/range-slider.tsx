import * as React from "react"
import { Slider as SliderPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

interface RangeSliderProps
  extends Omit<React.ComponentProps<typeof SliderPrimitive.Root>, "value" | "onValueChange"> {
  value: [number, number]
  onValueChange: (value: [number, number]) => void
}

function RangeSlider({
  className,
  value,
  onValueChange,
  ...props
}: RangeSliderProps) {
  return (
    <SliderPrimitive.Root
      data-slot="range-slider"
      className={cn(
        "relative flex w-full touch-none items-center select-none",
        className,
      )}
      value={value}
      onValueChange={onValueChange as (value: number[]) => void}
      {...props}
    >
      <SliderPrimitive.Track className="bg-primary/20 relative h-1.5 w-full grow rounded-full">
        <SliderPrimitive.Range className="bg-primary absolute h-full rounded-full" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="border-primary/50 bg-background block size-4 rounded-full border shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 hover:border-primary cursor-grab active:cursor-grabbing" />
      <SliderPrimitive.Thumb className="border-primary/50 bg-background block size-4 rounded-full border shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 hover:border-primary cursor-grab active:cursor-grabbing" />
    </SliderPrimitive.Root>
  )
}

export { RangeSlider }
