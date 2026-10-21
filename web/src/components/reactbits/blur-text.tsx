// BlurText, adapted from React Bits (reactbits.dev, TextAnimations/BlurText; MIT + Commons
// Clause, see LICENSE.md in this folder): words come in from a blur, one after another.
// Trimmed to the "blur" effect; renders as any element (the page's <h1>), with the whole
// text for screen readers. Motion's reducedMotion="user" turns it into a plain fade.
import { motion, MotionConfig, type Transition } from "motion/react";
import { useEffect, useMemo, useRef, useState, type ElementType } from "react";
import { cn } from "@/lib/utils";

interface BlurTextProps {
  text: string;
  as?: ElementType;
  /** Milliseconds between words. */
  delay?: number;
  className?: string;
  direction?: "top" | "bottom";
  trigger?: "view" | "load";
  stepDuration?: number;
  blur?: number;
  distance?: number;
  id?: string;
}

// Keeps a word and the following space together.
const NBSP = String.fromCharCode(160);
const blurTo = (px: number) => `blur(${Math.max(0, px)}px)`;

export default function BlurText({
  text,
  as: Tag = "p",
  delay = 120,
  className,
  direction = "top",
  trigger = "load",
  stepDuration = 0.35,
  blur = 10,
  distance = 40,
  id,
}: BlurTextProps) {
  const ref = useRef<HTMLElement>(null);
  const [inView, setInView] = useState(trigger === "load");

  const sign = direction === "top" ? -1 : 1;
  const from = { filter: blurTo(blur), opacity: 0, y: sign * distance };
  const keyframes = {
    filter: [from.filter, blurTo(blur / 2), blurTo(0)],
    opacity: [0, 0.5, 1],
    y: [from.y, (-sign * distance) / 10, 0],
  };
  const words = useMemo(() => text.split(" "), [text]);

  useEffect(() => {
    if (inView) return undefined;
    const element = ref.current;
    if (!element) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [inView]);

  const transition = (i: number): Transition => ({
    duration: stepDuration * 2,
    times: [0, 0.5, 1],
    delay: (i * delay) / 1000,
    ease: "linear",
    y: { type: "spring", visualDuration: stepDuration, bounce: 0.35, delay: (i * delay) / 1000 },
  });

  return (
    <MotionConfig reducedMotion="user">
      <Tag ref={ref} id={id} className={cn("flex flex-wrap", className)}>
        <span className="sr-only">{text}</span>
        {words.map((word, i) => (
          <motion.span
            aria-hidden="true"
            key={`${word}-${i}`}
            className="inline-block will-change-[transform,filter,opacity]"
            initial={from}
            animate={inView ? keyframes : from}
            transition={transition(i)}
          >
            {i < words.length - 1 ? `${word}${NBSP}` : word}
          </motion.span>
        ))}
      </Tag>
    </MotionConfig>
  );
}
