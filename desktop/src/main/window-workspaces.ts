type CrossWorkspaceWindow = {
  setVisibleOnAllWorkspaces: (
    visible: boolean,
    options?: {
      visibleOnFullScreen?: boolean;
      skipTransformProcessType?: boolean;
    },
  ) => void;
};

export default function showOnEveryWorkspace(
  target: CrossWorkspaceWindow,
  platform: string = process.platform,
): void {
  if (platform !== 'darwin') return;
  target.setVisibleOnAllWorkspaces(true, {
    visibleOnFullScreen: true,
    skipTransformProcessType: true,
  });
}
