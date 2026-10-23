import { SpaceDetail } from "@/features/research/space-detail";

export default async function ResearchSpacePage({
  params,
}: {
  params: Promise<{ spaceId: string }>;
}) {
  const { spaceId } = await params;
  return <SpaceDetail spaceId={spaceId} />;
}
