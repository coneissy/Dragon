import { useEffect, useRef, useState } from "react";

interface PriceCellProps {
  value: number | undefined;
}

export function PriceCell({ value }: PriceCellProps) {
  const prevRef = useRef(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value == null || prevRef.current == null) {
      prevRef.current = value;
      return;
    }
    if (value !== prevRef.current) {
      setFlash(value > prevRef.current ? "up" : "down");
      prevRef.current = value;
      const t = setTimeout(() => setFlash(null), 600);
      return () => clearTimeout(t);
    }
  }, [value]);

  if (value == null) return <td className="price-cell">--</td>;

  const formatted = value >= 1 ? value.toFixed(4) : value.toFixed(8);

  const className = [
    "price-cell",
    flash === "up" && "price-cell--flash-up",
    flash === "down" && "price-cell--flash-down",
  ]
    .filter(Boolean)
    .join(" ");

  return <td className={className}>{formatted}</td>;
}
