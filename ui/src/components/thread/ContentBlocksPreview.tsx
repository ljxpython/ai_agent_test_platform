import { cn } from "@/lib/utils";
import type { ContentBlock } from "@langchain/core/messages";
import type { FC, ReactNode } from "react";
import { MultimodalPreview } from "./MultimodalPreview";

interface ContentBlocksPreviewProps {
  blocks: ContentBlock.Multimodal.Data[];
  onRemove: (key: string) => void;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function getContentBlockPreviewKey(
  block: ContentBlock.Multimodal.Data,
): string {
  return `${block.type}:${block.mimeType}:${String(block.data ?? "").slice(0, 32)}`;
}

/**
 * Renders a preview of content blocks with optional remove functionality.
 * Uses cn utility for robust class merging.
 */
export const ContentBlocksPreview: FC<ContentBlocksPreviewProps> = ({
  blocks,
  onRemove,
  size = "md",
  className,
}) => {
  if (!blocks.length) return null;

  const previews: ReactNode[] = [];
  for (const block of blocks) {
    const key = getContentBlockPreviewKey(block);
    previews.push(
      <MultimodalPreview
        key={key}
        block={block}
        removable
        onRemove={() => onRemove(key)}
        size={size}
      />,
    );
  }

  return (
    <div className={cn("flex flex-wrap gap-2 p-3.5 pb-0", className)}>
      {previews}
    </div>
  );
};
