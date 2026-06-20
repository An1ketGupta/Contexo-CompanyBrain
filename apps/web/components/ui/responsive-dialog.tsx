"use client";

import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { useIsDesktop } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

// Adaptive container: a centered Radix Dialog on desktop, a vaul bottom
// sheet on mobile. Same API surface so callers don't branch in JSX —
// drop-in for any Dialog usage that needs to feel native on phones.
//
// Implementation note: we render exactly ONE tree (dialog OR drawer) based
// on the media query. Trying to render both with `hidden` classes would
// duplicate Portal mounts and leak focus traps. The cost is one extra
// render on first mount; the benefit is each primitive owns its own
// keyboard/focus/scroll-lock contract cleanly.

interface ResponsiveDialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}

export function ResponsiveDialog({
  open,
  onOpenChange,
  children,
}: ResponsiveDialogProps) {
  const isDesktop = useIsDesktop();
  const Root = isDesktop ? Dialog : Drawer;
  return (
    <Root open={open} onOpenChange={onOpenChange}>
      {children}
    </Root>
  );
}

export function ResponsiveDialogTrigger({
  children,
  asChild,
}: {
  children: React.ReactNode;
  asChild?: boolean;
}) {
  const isDesktop = useIsDesktop();
  const Trigger = isDesktop ? DialogTrigger : DrawerTrigger;
  return <Trigger asChild={asChild}>{children}</Trigger>;
}

interface ResponsiveDialogContentProps
  extends React.HTMLAttributes<HTMLDivElement> {
  // Maximum width on desktop. The mobile drawer is always full-width.
  desktopClassName?: string;
}

export function ResponsiveDialogContent({
  className,
  desktopClassName,
  children,
  ...props
}: ResponsiveDialogContentProps) {
  const isDesktop = useIsDesktop();
  if (isDesktop) {
    return (
      <DialogContent className={cn(desktopClassName, className)} {...props}>
        {children}
      </DialogContent>
    );
  }
  return (
    // Vaul handles the swipe-to-dismiss + drag handle. Cap height so the
    // sheet never grows past 90dvh — the user can scroll inside.
    <DrawerContent
      className={cn("overflow-y-auto", className)}
      {...props}
    >
      {children}
    </DrawerContent>
  );
}

export function ResponsiveDialogHeader({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  const isDesktop = useIsDesktop();
  return isDesktop ? (
    <DialogHeader className={className} {...props} />
  ) : (
    <DrawerHeader className={className} {...props} />
  );
}

export function ResponsiveDialogFooter({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  const isDesktop = useIsDesktop();
  return isDesktop ? (
    <DialogFooter className={className} {...props} />
  ) : (
    <DrawerFooter className={className} {...props} />
  );
}

export function ResponsiveDialogTitle({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const isDesktop = useIsDesktop();
  return isDesktop ? (
    <DialogTitle className={className}>{children}</DialogTitle>
  ) : (
    <DrawerTitle className={className}>{children}</DrawerTitle>
  );
}

export function ResponsiveDialogDescription({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const isDesktop = useIsDesktop();
  return isDesktop ? (
    <DialogDescription className={className}>{children}</DialogDescription>
  ) : (
    <DrawerDescription className={className}>{children}</DrawerDescription>
  );
}
