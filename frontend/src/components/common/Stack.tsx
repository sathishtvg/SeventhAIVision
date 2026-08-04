import { forwardRef } from 'react'
import MuiStack, { type StackProps as MuiStackProps } from '@mui/material/Stack'

/**
 * MUI v9's Stack dropped support for common flexbox props (alignItems,
 * justifyContent, flexWrap, gap) as direct component props — only
 * direction/spacing/divider/component/sx remain first-class (confirmed
 * against Stack.js's propTypes, which no longer lists them). This app has
 * ~70 call sites written against the old v5/v6 API where those props were
 * resolved directly, no `sx` needed — passing them now silently no-ops
 * and leaks the prop onto the DOM as an invalid attribute. Rather than
 * rewrite every call site, this wrapper folds the legacy props into `sx`
 * before forwarding to the real Stack, restoring the old ergonomics
 * transparently for both existing and future call sites.
 */
export interface StackProps extends MuiStackProps {
  alignItems?: React.CSSProperties['alignItems']
  justifyContent?: React.CSSProperties['justifyContent']
  flexWrap?: React.CSSProperties['flexWrap']
  gap?: React.CSSProperties['gap'] | number
}

const Stack = forwardRef<HTMLDivElement, StackProps>(
  ({ alignItems, justifyContent, flexWrap, gap, sx, ...rest }, ref) => (
    <MuiStack
      ref={ref}
      sx={[
        {
          ...(alignItems !== undefined && { alignItems }),
          ...(justifyContent !== undefined && { justifyContent }),
          ...(flexWrap !== undefined && { flexWrap }),
          ...(gap !== undefined && { gap }),
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
      {...rest}
    />
  ),
)
Stack.displayName = 'Stack'

export default Stack
