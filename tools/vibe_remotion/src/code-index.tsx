import React from 'react';
import {Composition, getInputProps, registerRoot} from 'remotion';
import {CodeScene, type CodeProps} from './CodeScene';

const Root:React.FC = () => {
  const input=getInputProps() as CodeProps;
  return <Composition id="OCVCode" component={CodeScene} width={input.canvas[0]} height={input.canvas[1]} fps={input.fps}
    durationInFrames={Math.max(1,Math.round(input.duration*input.fps))} defaultProps={input}/>;
};
registerRoot(Root);
