import { Composition } from "remotion";
import { FPS, Promo, RECEIPT_CLIP, ReceiptClip, TOTAL } from "./Promo";
import "./video.css";

// One composition per language and shape: 16:9 for GitHub and X, 9:16 for
// Douyin, Xiaohongshu and Reels.
const SHAPES = [
  { key: "wide", width: 1920, height: 1080 },
  { key: "tall", width: 1080, height: 1920 },
] as const;

export function Root() {
  return (
    <>
      {(["zh", "en"] as const).flatMap((lang) =>
        SHAPES.map((s) => (
          <Composition
            key={`${lang}-${s.key}`}
            id={`promo-${lang}-${s.key}`}
            component={Promo}
            durationInFrames={TOTAL}
            fps={FPS}
            width={s.width}
            height={s.height}
            defaultProps={{ lang }}
          />
        )),
      )}
      {(["zh", "en"] as const).map((lang) => (
        <Composition
          key={`receipt-${lang}`}
          id={`clip-receipt-${lang}`}
          component={ReceiptClip}
          durationInFrames={RECEIPT_CLIP}
          fps={FPS}
          width={1920}
          height={1080}
          defaultProps={{ lang }}
        />
      ))}
    </>
  );
}
