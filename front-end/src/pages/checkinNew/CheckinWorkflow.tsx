import { useCheckinWorkflowController } from "./useCheckinWorkflowController";
import { CommittedItemView } from "./CommittedItemView";
import { EditRevisionView } from "./EditRevisionView";
import { UploadWorkflowView } from "./UploadWorkflowView";

export function CheckinWorkflow(props: Parameters<typeof useCheckinWorkflowController>[0] = {}) {
  const controller = useCheckinWorkflowController(props);
  if (!controller.editMode && controller.committedItemId) return <CommittedItemView controller={controller} />;
  if (controller.editMode) return <EditRevisionView controller={controller} />;
  return <UploadWorkflowView controller={controller} />;
}
