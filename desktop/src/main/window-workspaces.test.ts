import showOnEveryWorkspace from './window-workspaces';

describe('showOnEveryWorkspace', () => {
  it('keeps Coco visible across macOS Spaces and fullscreen windows', () => {
    const target = { setVisibleOnAllWorkspaces: jest.fn() };

    showOnEveryWorkspace(target, 'darwin');

    expect(target.setVisibleOnAllWorkspaces).toHaveBeenCalledWith(true, {
      visibleOnFullScreen: true,
      skipTransformProcessType: true,
    });
  });

  it('does not apply macOS workspace behavior on other platforms', () => {
    const target = { setVisibleOnAllWorkspaces: jest.fn() };

    showOnEveryWorkspace(target, 'win32');

    expect(target.setVisibleOnAllWorkspaces).not.toHaveBeenCalled();
  });
});
