import { NavLink } from "react-router-dom";
import type { ObjectTypeSummary } from "../api/objectTypes";
import { cx } from "../ui/cx";
import { navLinkActiveClass, navLinkClass, navLinkRestClass } from "../ui/classes";

/** Presentational nav list, one entry per live object type (`list_object_types`). */
export function ObjectTypeNav({ objectTypes }: { objectTypes: ObjectTypeSummary[] }) {
  return (
    <nav aria-label="Object types" className="flex items-center">
      <ul className="flex items-center gap-0.5">
        {objectTypes.map((objectType) => (
          <li key={objectType.key}>
            <NavLink
              to={`/${objectType.key}`}
              className={({ isActive }) =>
                cx(navLinkClass, isActive ? navLinkActiveClass : navLinkRestClass)
              }
            >
              {objectType.name}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
